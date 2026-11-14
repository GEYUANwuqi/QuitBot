"""Async scheduling over one dedicated desktop worker per QQ instance."""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .errors import InstanceClosed, QuitBotError, TargetMismatch
from .instance import QQInstance
from .models import Message, MessageCursor, message_ready


class AsyncQQInstance:
    async def notifications(self, kind="group"):
        return await self._call(None, "notifications", kind)

    async def respond_request(self, kind, ui_key, *, approve):
        return await self._call(None, "respond_request", kind, ui_key, approve=approve)

    def __init__(self, *, factory=QQInstance, initialize_com=True, queue_limit=64):
        if queue_limit < 1:
            raise ValueError("queue_limit must be positive")
        self._factory = factory
        self._initialize_com = initialize_com
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="quitbot-uia"
        )
        self._slots = asyncio.Semaphore(queue_limit)
        self._instance = None
        self._closed = False
        self._closing = False
        self._stop = asyncio.Event()
        self._handles = {}

    @classmethod
    async def connect(cls, account=None, *, hwnd=None, targets=None, **options):
        self = cls(**options)
        try:
            await self._submit(self._create, account, hwnd, targets)
        except BaseException:
            await self.close()
            raise
        return self

    def _create(self, account, hwnd, targets):
        from .platforms import initialize_thread

        self._com = initialize_thread(self._initialize_com)
        kwargs = {"hwnd": hwnd}
        if targets is not None:
            kwargs["targets"] = targets
        self._instance = self._factory(account, **kwargs)
        self.account = self._instance.account
        self.hwnd = self._instance.hwnd
        self.verified_id = getattr(self._instance, "verified_id", None)
        self.targets = tuple(self._instance._client.targets)

    async def _submit(self, fn, *args):
        async with self._slots:
            # Pending futures may be cancelled. A running UI action cannot be
            # interrupted; callers must inspect before retrying cancelled sends.
            return await asyncio.wrap_future(self._executor.submit(fn, *args))

    def _dispatch(self, target_id, method, args, kwargs):
        qq = self._instance
        if qq is None:
            raise InstanceClosed("Async instance is closed")
        if target_id is None:
            return getattr(qq, method)(*args, **kwargs)
        conversation = qq.conversation(target_id)
        if method == "reply":
            return conversation.get_message(args[0]).reply(args[1]).receipt
        if method == "message_actions":
            return conversation.get_message(args[0]).actions()
        if method in (
            "essence_state",
            "set_essence",
            "recall",
            "reactions",
            "add_reaction",
            "remove_own_reaction",
            "contact_profile",
        ):
            return getattr(conversation.get_message(args[0]), method)(
                *args[1:], **kwargs
            )
        if method in ("message_secondary", "reaction_palette"):
            message = conversation.get_message(args[0])
            return getattr(
                message,
                "secondary_controls" if method == "message_secondary" else method,
            )()
        if method == "forward":
            return (
                conversation.get_message(args[0])
                .forward_to(qq.conversation(args[1]))
                .receipt
            )
        result = getattr(conversation, method)(*args, **kwargs)
        if method in (
            "send_text",
            "send_image",
            "send_file",
            "send_face",
            "send_mention",
            "send_draft",
        ):
            return result.receipt
        if method in ("messages", "detached_messages"):
            return [m.data for m in result]
        if method == "get_message":
            return result.data
        return result

    async def _call(self, target_id, method, *args, **kwargs):
        if self._closed or self._closing:
            raise InstanceClosed("Async instance is closed")
        return await self._submit(self._dispatch, target_id, method, args, kwargs)

    def conversation(self, key):
        if self._closed or self._closing:
            raise InstanceClosed("Async instance is closed")
        if isinstance(key, AsyncConversation):
            if key.instance is not self:
                raise TargetMismatch("Conversation belongs to another async instance")
            return key
        matches = [t for t in self.targets if str(key) in (t.id, t.name)]
        if len(matches) != 1:
            raise TargetMismatch("Unknown or ambiguous configured conversation")
        target = matches[0]
        if target.id not in self._handles:
            self._handles[target.id] = AsyncConversation(self, target)
        return self._handles[target.id]

    def group(self, key):
        result = self.conversation(key)
        if result.target.kind != "group":
            raise TargetMismatch("Expected group")
        return result

    def friend(self, key):
        result = self.conversation(key)
        if result.target.kind != "private":
            raise TargetMismatch("Expected private conversation")
        return result

    async def status(self):
        return await self._call(None, "status")

    async def menus(self, area):
        return await self._call(None, "menus", area)

    async def client_version(self):
        return await self._call(None, "client_version")

    async def storage_paths(self):
        return await self._call(None, "storage_paths")

    async def identify(self):
        return await self._call(None, "identify")

    async def search(self, query, *, limit=30):
        return await self._call(None, "search", query, limit=limit)

    async def discover_conversations(self):
        return await self._call(None, "discover_conversations")

    async def resolve_conversation(self, name):
        return await self._call(None, "resolve_conversation", name)

    async def events(
        self,
        conversations,
        *,
        mode="detached",
        interval=1.0,
        include_existing=False,
        duration=None,
        on_ready=None,
        refresh_detached=False,
    ):
        if mode not in ("detached", "switch") or interval < 0.2:
            raise ValueError("mode must be detached/switch and interval >= 0.2")
        if type(refresh_detached) is not bool or (
            refresh_detached and mode != "detached"
        ):
            raise ValueError("refresh_detached requires detached mode and a boolean")
        scopes = list(dict.fromkeys(self.conversation(c) for c in conversations))
        if not scopes:
            raise ValueError("Provide conversations")
        self._stop.clear()
        cursor = MessageCursor()

        async def read(scope):
            method = "detached_messages" if mode == "detached" else "messages"
            options = {"refresh": True} if refresh_detached else {}
            return await self._call(scope.id, method, **options)

        for scope in scopes:
            try:
                if mode == "detached":
                    await scope.open_detached()
                baseline = await read(scope)
            except QuitBotError as error:
                raise type(error)(
                    f"初始化监听「{scope.name}」({scope.id})失败：{error}"
                ) from error
            cursor.prime(baseline)
            if include_existing:
                for message in baseline:
                    if message.direction == "incoming":
                        yield AsyncMessageEvent(scope, message)
        if on_ready:
            result = on_ready()
            if hasattr(result, "__await__"):
                await result
        started = time.monotonic()
        while not self._stop.is_set() and not self._closing and not self._closed:
            if duration is not None and time.monotonic() - started >= duration:
                break
            for scope in scopes:
                if self._stop.is_set():
                    break
                eligible = [
                    m
                    for m in await read(scope)
                    if m.direction == "incoming" and message_ready(m)
                ]
                for message in cursor.take(eligible):
                    yield AsyncMessageEvent(scope, message)
            try:
                await asyncio.wait_for(self._stop.wait(), interval)
            except TimeoutError:
                pass

    async def activities(
        self,
        conversations,
        *,
        kinds=("group", "friend"),
        mode="detached",
        interval=2.0,
        duration=None,
        on_ready=None,
        track_roster=False,
        refresh_detached=False,
    ):
        """Observe new UI system rows/cards; optional loaded-roster differences."""
        from .domain.activities import ActivityCursor, activity_message_key

        if (
            mode not in ("detached", "switch")
            or interval < 0.2
            or any(k not in ("group", "friend") for k in kinds)
        ):
            raise ValueError("Invalid activity polling options")
        if type(refresh_detached) is not bool or (
            refresh_detached and mode != "detached"
        ):
            raise ValueError("refresh_detached requires detached mode and a boolean")
        scopes = [self.conversation(c) for c in conversations]
        cards = {kind: ActivityCursor() for kind in dict.fromkeys(kinds)}
        cursors = {scope.id: ActivityCursor() for scope in scopes}
        rosters = {}
        for scope in scopes:
            if mode == "detached":
                await scope.open_detached()
        ready = False
        started = time.monotonic()
        while not self._closing and not self._closed:
            for kind, cursor in cards.items():
                for card in cursor.take(
                    await self.notifications(kind), lambda x: x.ui_key
                ):
                    yield {"kind": "request_card", "card": card}
            for scope in scopes:
                method = "detached_messages" if mode == "detached" else "messages"
                options = {"refresh": True} if refresh_detached else {}
                rows = await self._call(scope.id, method, **options)
                for message in cursors[scope.id].take(rows, activity_message_key):
                    if message.direction == "system":
                        yield {
                            "kind": "system_message",
                            "target": scope.target,
                            "message": message,
                        }
                if track_roster and scope.target.kind == "group":
                    members = tuple(
                        sorted((m.name, m.role) for m in await scope.members())
                    )
                    if scope.id in rosters and members != rosters[scope.id]:
                        yield {
                            "kind": "roster_change",
                            "target": scope.target,
                            "before": rosters[scope.id],
                            "after": members,
                        }
                    rosters[scope.id] = members
            if not ready:
                ready = True
                if on_ready:
                    result = on_ready()
                    if hasattr(result, "__await__"):
                        await result
            if duration is not None and time.monotonic() - started >= duration:
                break
            await asyncio.sleep(interval)

    def stop(self):
        """Call on the asyncio loop; does not interrupt an in-flight UI call."""
        self._stop.set()

    def _dispose(self):
        try:
            if self._instance is not None:
                self._instance.close()
                self._instance = None
        finally:
            from .platforms import close_automation

            close_automation(getattr(self, "_com", None))
            self._com = None

    async def close(self):
        if self._closed:
            return
        if self._closing:
            await self._close_task
            return
        self._closing = True
        self.stop()

        async def finish():
            try:
                await self._submit(self._dispose)
            finally:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._closed = True

        self._close_task = asyncio.create_task(finish())
        await asyncio.shield(self._close_task)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()


class AsyncConversation:
    async def history_calendar(self, year, month):
        return await self.instance._call(self.id, "history_calendar", year, month)

    async def locate_history_image(self, on_date, *, expected_message_id):
        return await self.instance._call(
            self.id,
            "locate_history_image",
            on_date,
            expected_message_id=expected_message_id,
        )

    async def favorite_stickers(self):
        return await self.instance._call(self.id, "favorite_stickers")

    async def import_stickers(self, paths):
        return await self.instance._call(self.id, "import_stickers", paths)

    async def move_sticker_first(self, selector):
        return await self.instance._call(self.id, "move_sticker_first", selector)

    async def sticker_remark(self, selector):
        return await self.instance._call(self.id, "sticker_remark", selector)

    async def set_sticker_remark(self, selector, value):
        return await self.instance._call(self.id, "set_sticker_remark", selector, value)

    async def delete_sticker(self, selector):
        return await self.instance._call(self.id, "delete_sticker", selector)

    async def folder_properties(self, name, *, expected=None):
        return await self.instance._call(
            self.id, "folder_properties", name, expected=expected
        )

    async def download_files(self, names, destination, *, expected=None):
        return await self.instance._call(
            self.id, "download_files", names, destination, expected=expected
        )

    async def delete_files(self, names, *, expected=None):
        return await self.instance._call(self.id, "delete_files", names, expected=expected)

    async def download_folder(self, name, download_root, *, expected=None):
        return await self.instance._call(
            self.id, "download_folder", name, download_root, expected=expected
        )

    async def save_qr(self, destination):
        return await self.instance._call(self.id, "save_qr", destination)

    async def share_qr_to(self, destination):
        target = self.instance.conversation(destination)
        return await self.instance._call(self.id, "share_qr_to", target.id)

    async def profile(self, *, full=False):
        return await self.instance._call(self.id, "profile", full=full)

    async def like(self):
        return await self.instance._call(self.id, "like")

    async def share_to(self, destination):
        target = self.instance.conversation(destination)
        return await self.instance._call(self.id, "share_to", target.id)

    async def contact_profile(self, message_id, *, expected_user_id):
        return await self.instance._call(
            self.id, "contact_profile", message_id, expected_user_id=expected_user_id
        )

    def __init__(self, instance, target):
        self.instance, self.target = instance, target
        self.id, self.name = target.id, target.name

    async def open_detached(self):
        return await self.instance._call(self.id, "open_detached")

    async def info(self):
        return await self.instance._call(self.id, "info")

    async def messages(self):
        return await self.instance._call(self.id, "messages")

    async def detached_messages(self, *, refresh=False):
        if type(refresh) is not bool:
            raise ValueError("refresh must be a boolean")
        options = {"refresh": True} if refresh else {}
        return await self.instance._call(self.id, "detached_messages", **options)

    async def download_message_file(self, message_id, destination, *, expected_name):
        return await self.instance._call(self.id, "download_message_file", message_id, destination, expected_name=expected_name)

    async def export_face_ids(self, message_id):
        return await self.instance._call(self.id, "export_face_ids", message_id)

    async def export_images(self, message_id):
        return await self.instance._call(self.id, "export_images", message_id)

    async def get_message(self, message_id):
        return await self.instance._call(self.id, "get_message", message_id)

    async def send_text(self, text):
        return await self.instance._call(self.id, "send_text", text)

    async def send_image(self, path):
        return await self.instance._call(self.id, "send_image", path)

    async def mark_unread(self):
        return await self.instance._call(self.id, "mark_unread")

    async def send_face(self, label, *, text=""):
        return await self.instance._call(self.id, "send_face", label, text=text)

    async def essences(self):
        return await self.instance._call(self.id, "essences")

    async def send_file(self, path):
        return await self.instance._call(self.id, "send_file", path)

    async def reply(self, message_id, text):
        return await self.instance._call(self.id, "reply", message_id, text)

    async def members(self, **kwargs):
        return await self.instance._call(self.id, "members", **kwargs)

    async def member_profile(self, name):
        return await self.instance._call(self.id, "member_profile", name)

    async def member_actions(self, name):
        return await self.instance._call(self.id, "member_actions", name)

    async def set_member_ban(self, name, duration):
        return await self.instance._call(self.id, "set_member_ban", name, duration)

    async def set_member_admin(self, name, enabled, *, expected_user_id):
        return await self.instance._call(
            self.id,
            "set_member_admin",
            name,
            enabled,
            expected_user_id=expected_user_id,
        )

    async def kick_member(self, name, *, expected_user_id):
        return await self.instance._call(
            self.id, "kick_member", name, expected_user_id=expected_user_id
        )

    async def leave(self):
        return await self.instance._call(self.id, "leave")

    async def application_menu(self):
        return await self.instance._call(self.id, "application_menu")

    async def member_submenu(self, member, entry):
        return await self.instance._call(self.id, "member_submenu", member, entry)

    async def files(self, *, query=None, order=None):
        options = {"query": query}
        if order is not None:
            options["order"] = order
        return await self.instance._call(self.id, "files", **options)

    async def file_actions(self, row_index, *, query=None):
        return await self.instance._call(
            self.id, "file_actions", row_index, query=query
        )

    async def notices(self):
        return await self.instance._call(self.id, "notices")

    async def file_entries(self, *, folder=None):
        return await self.instance._call(self.id, "file_entries", folder=folder)

    async def create_folder(self, name):
        return await self.instance._call(self.id, "create_folder", name)

    async def rename_folder(self, name, new_name, *, expected=None):
        return await self.instance._call(
            self.id, "rename_folder", name, new_name, expected=expected
        )

    async def delete_folder(self, name, *, expected=None):
        return await self.instance._call(
            self.id, "delete_folder", name, expected=expected
        )

    async def upload_file(self, path):
        return await self.instance._call(self.id, "upload_file", path)

    async def move_file(self, name, folder, *, source_folder=None, expected=None):
        return await self.instance._call(
            self.id,
            "move_file",
            name,
            folder,
            source_folder=source_folder,
            expected=expected,
        )

    async def rename_file(self, name, new_name, *, expected=None):
        return await self.instance._call(
            self.id, "rename_file", name, new_name, expected=expected
        )

    async def delete_file(self, name, *, expected=None):
        return await self.instance._call(
            self.id, "delete_file", name, expected=expected
        )

    async def download_file(self, name, destination, *, expected=None):
        return await self.instance._call(
            self.id, "download_file", name, destination, expected=expected
        )

    async def file_sort_options(self):
        return await self.instance._call(self.id, "file_sort_options")

    async def file_sort_state(self):
        return await self.instance._call(self.id, "file_sort_state")

    async def controls(self, *, area="chat"):
        return await self.instance._call(self.id, "controls", area=area)

    async def menus(self, area):
        return await self.instance._call(self.id, "menus", area)

    async def action_plan(self, action, *, area="settings"):
        return await self.instance._call(self.id, "action_plan", action, area=area)

    async def send_mention(self, member, text):
        return await self.instance._call(self.id, "send_mention", str(member), text)

    async def settings(self):
        return await self.instance._call(self.id, "settings")

    async def set_pinned(self, enabled):
        return await self.instance._call(self.id, "set_pinned", enabled)

    async def set_muted(self, enabled):
        return await self.instance._call(self.id, "set_muted", enabled)

    async def join_policy(self):
        return await self.instance._call(self.id, "join_policy")

    async def tags(self):
        return await self.instance._call(self.id, "tags")

    async def share_link(self):
        return await self.instance._call(self.id, "share_link")

    async def poke(self):
        return await self.instance._call(self.id, "poke")

    async def set_tag(self, label, enabled=True):
        return await self.instance._call(self.id, "set_tag", label, enabled)

    async def description(self):
        return await self.instance._call(self.id, "description")

    async def set_description(self, value):
        return await self.instance._call(self.id, "set_description", value)

    async def publish_notice(self, text, *, image=None):
        return await self.instance._call(self.id, "publish_notice", text, image=image)

    async def edit_notice(self, text, new_text, *, image=None, remove_image=False, expected=None):
        return await self.instance._call(
            self.id,
            "edit_notice",
            text,
            new_text,
            image=image,
            remove_image=remove_image,
            expected=expected,
        )

    async def delete_notice(self, text, *, expected=None):
        return await self.instance._call(self.id, "delete_notice", text, expected=expected)

    async def notice_pinned(self, text, *, expected=None):
        return await self.instance._call(self.id, "notice_pinned", text, expected=expected)

    async def set_notice_pinned(self, text, enabled, *, expected=None):
        return await self.instance._call(self.id, "set_notice_pinned", text, enabled, expected=expected)

    async def set_join_mode(self, value):
        return await self.instance._call(self.id, "set_join_mode", value)

    async def set_member_invites(self, value):
        return await self.instance._call(self.id, "set_member_invites", value)

    async def set_invite_approval(self, value):
        return await self.instance._call(self.id, "set_invite_approval", value)

    async def set_bot_invites(self, value):
        return await self.instance._call(self.id, "set_bot_invites", value)

    async def set_bot_invite_approval(self, value):
        return await self.instance._call(self.id, "set_bot_invite_approval", value)

    async def preferences(self):
        return await self.instance._call(self.id, "preferences")

    async def set_whole_ban(self, enabled):
        return await self.instance._call(self.id, "set_whole_ban", enabled)

    async def set_speak_limit(self, per_minute):
        return await self.instance._call(self.id, "set_speak_limit", per_minute)

    async def set_message_mode(self, mode):
        return await self.instance._call(self.id, "set_message_mode", mode)

    async def auto_download(self):
        return await self.instance._call(self.id, "auto_download")

    async def set_auto_download(self, enabled):
        return await self.instance._call(self.id, "set_auto_download", enabled)

    async def set_blocked(self, enabled):
        return await self.instance._call(self.id, "set_blocked", enabled)

    async def set_own_card(self, value):
        return await self.instance._call(self.id, "set_own_card", value)

    async def poke_member(self, name, *, expected_user_id):
        return await self.instance._call(
            self.id, "poke_member", name, expected_user_id=expected_user_id
        )

    async def set_member_card(self, name, value, *, expected_user_id):
        return await self.instance._call(
            self.id, "set_member_card", name, value, expected_user_id=expected_user_id
        )

    async def draft(self):
        return await self.instance._call(self.id, "draft")

    async def set_draft(self, text):
        return await self.instance._call(self.id, "set_draft", text)

    async def clear_draft(self, *, expected_text):
        return await self.instance._call(
            self.id, "clear_draft", expected_text=expected_text
        )

    async def send_draft(self, *, expected_text):
        return await self.instance._call(
            self.id, "send_draft", expected_text=expected_text
        )

    async def message_actions(self, message_id):
        return await self.instance._call(self.id, "message_actions", message_id)

    async def essence_state(self, message_id):
        return await self.instance._call(self.id, "essence_state", message_id)

    async def set_essence(self, message_id, enabled):
        return await self.instance._call(self.id, "set_essence", message_id, enabled)

    async def recall(self, message_id, *, expected_text=None):
        return await self.instance._call(
            self.id, "recall", message_id, expected_text=expected_text
        )

    async def message_secondary(self, message_id):
        return await self.instance._call(self.id, "message_secondary", message_id)

    async def reaction_palette(self, message_id):
        return await self.instance._call(self.id, "reaction_palette", message_id)

    async def reactions(self, message_id):
        return await self.instance._call(self.id, "reactions", message_id)

    async def add_reaction(self, message_id, label, *, expected_text=None):
        return await self.instance._call(
            self.id, "add_reaction", message_id, label, expected_text=expected_text
        )

    async def remove_own_reaction(self, message_id, *, expected_text=None):
        return await self.instance._call(
            self.id, "remove_own_reaction", message_id, expected_text=expected_text
        )

    async def forward(self, message_id, destination):
        target = self.instance.conversation(destination)
        return await self.instance._call(self.id, "forward", message_id, target.id)

    async def history(self, **kwargs):
        return await self.instance._call(self.id, "history", **kwargs)


@dataclass(frozen=True)
class AsyncMessageEvent:
    conversation: AsyncConversation
    message: Message

    @property
    def text(self):
        return self.message.text

    async def reply(self, text, *, quote=False):
        if quote:
            return await self.conversation.reply(self.message.id, text)
        return await self.conversation.send_text(text)
