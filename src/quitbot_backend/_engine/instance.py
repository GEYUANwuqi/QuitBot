"""Instance-owned object model over the native UIA client."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from .client import QQClient
from .core.capabilities import (
    CAPABILITIES,
    Capability,  # noqa: F401 - compatibility import
)
from .errors import InstanceClosed, TargetMismatch
from .models import (
    ConversationInfo,
    GroupMemberInfo,
    HistoryRecord,
    InstanceStatus,
    Message,
    Target,
)


class QQInstance:
    """One bound QQ window/account owns all conversations, messages and events.

    close() disposes SDK handles only; it never logs out or closes QQ.
    """

    def __init__(self, account=None, *, hwnd=None, targets=None):
        verified_id = None
        if account is None or str(account).isdigit():
            from .discovery import discover_windows, select_window

            selected = select_window(
                discover_windows(resolve_ids=True, hwnd=hwnd), account, hwnd=hwnd
            )
            account, hwnd, verified_id = (
                selected.nickname,
                selected.hwnd,
                selected.qq_id,
            )
        kwargs = {"hwnd": hwnd}
        if targets is not None:
            kwargs["targets"] = targets
        self._initialize(QQClient(account, **kwargs))
        self._client.backend.self_id = verified_id
        self.verified_id = verified_id

    def identify(self):
        from .discovery import read_self_id
        from .locking import ui_transaction

        self._assert_open()
        with ui_transaction():
            value = read_self_id(self._client.backend)
            self._client.backend.self_id = value
            return value

    @classmethod
    def from_client(cls, client: QQClient):
        instance = cls.__new__(cls)
        instance._initialize(client)
        return instance

    def _initialize(self, client):
        self._client = client
        self._closed = False
        self._handles = {}
        self._subscriptions = []
        self._stop = threading.Event()
        self.groups = ConversationCollection(self, "group")
        self.friends = ConversationCollection(self, "private")

    @property
    def account(self):
        return self._client.backend.account

    @property
    def hwnd(self):
        return self._client.backend.hwnd

    @property
    def closed(self):
        return self._closed

    @property
    def capabilities(self):
        return CAPABILITIES

    def supports(self, name):
        return any(c.name == name and c.available for c in self.capabilities)

    def menus(self, area):
        """Read the account's add/more menu without executing a leaf item."""
        self._assert_open()
        return self._client.control_ui.global_menu(area)

    def client_version(self):
        """Read the bound QQ client's visible version; this is not the SDK version."""
        self._assert_open()
        return self._client.control_ui.navigation.client_version()

    def storage_paths(self):
        """Read QQ's displayed download/data directories without opening them."""
        self._assert_open()
        return self._client.control_ui.navigation.storage_paths()

    def notifications(self, kind="group"):
        """Read loaded native notification cards; UI keys are local, not server IDs."""
        from .ui.workflows.notifications import NotificationUI

        self._assert_open()
        return NotificationUI(self._client).read(kind)

    def respond_request(self, kind, ui_key, *, approve):
        from .ui.workflows.notifications import NotificationUI

        self._assert_open()
        return NotificationUI(self._client).respond(kind, ui_key, approve)

    def _assert_open(self):
        if self._closed:
            raise InstanceClosed("This QQInstance is closed; create another instance")
        self._client.backend.check_thread()

    def conversation(self, key) -> Conversation:
        self._assert_open()
        if isinstance(key, Conversation):
            if key.instance is not self:
                raise TargetMismatch("Conversation belongs to another QQ instance")
            return key
        target = self._client.target(key)
        cache_key = (target.kind, target.id)
        if cache_key not in self._handles:
            cls = Group if target.kind == "group" else Friend
            self._handles[cache_key] = cls(self, target)
        return self._handles[cache_key]

    def group(self, key) -> Group:
        return self.groups.get(key)

    def friend(self, key) -> Friend:
        return self.friends.get(key)

    def search(self, query, *, limit=30):
        self._assert_open()
        return self._client.search(query, limit=limit)

    def discover_conversations(self):
        from .ui.workflows.recent import RecentConversations

        self._assert_open()
        return RecentConversations(self._client.backend).discover()

    def resolve_conversation(self, name):
        from .ui.workflows.recent import RecentConversations

        self._assert_open()
        return RecentConversations(self._client.backend).resolve(name)

    def status(self) -> InstanceStatus:
        self._assert_open()
        return self._client.instance_status()

    def on_message(
        self, handler: Callable[[MessageEvent], object], *, conversation=None
    ) -> Subscription:
        self._assert_open()
        if not callable(handler):
            raise TypeError("Message handler must be callable")
        scope = self.conversation(conversation) if conversation is not None else None
        subscription = Subscription(self, handler, scope)
        self._subscriptions.append(subscription)
        return subscription

    def events(
        self,
        conversations,
        *,
        interval=1.0,
        duration=None,
        include_existing=False,
        incoming_only=True,
        on_ready=None,
        mode="switch",
        refresh_detached=False,
    ) -> Iterator[MessageEvent]:
        self._assert_open()
        if type(refresh_detached) is not bool or (
            refresh_detached and mode != "detached"
        ):
            raise ValueError("refresh_detached requires detached mode and a boolean")
        scopes = [self.conversation(c) for c in conversations]
        if not scopes:
            raise ValueError("Provide at least one conversation")
        self._stop.clear()
        for data in self._client.watch(
            [c.target for c in scopes],
            interval=interval,
            duration=duration,
            include_existing=include_existing,
            incoming_only=incoming_only,
            on_ready=on_ready,
            stop_event=self._stop,
            mode=mode,
            refresh_detached=refresh_detached,
        ):
            self._assert_open()
            conversation = next(c for c in scopes if c.id == data.conversation_id)
            yield MessageEvent(
                conversation, MessageHandle(conversation, data.id, data=data)
            )

    def run(self, conversations, **kwargs):
        for event in self.events(conversations, **kwargs):
            for subscription in tuple(self._subscriptions):
                if self.closed or self._stop.is_set():
                    break
                if subscription.active and (
                    subscription.conversation is None
                    or subscription.conversation is event.conversation
                ):
                    subscription.handler(event)
            if self._stop.is_set():
                break

    def stop(self):
        """Thread-safe signal; the UI polling loop stops at its next check."""
        self._stop.set()

    def close(self):
        self._stop.set()
        self._closed = True
        for subscription in self._subscriptions:
            subscription.active = False
        self._subscriptions.clear()
        self._handles.clear()

    def __enter__(self):
        self._assert_open()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


class ConversationCollection:
    def __init__(self, instance, kind):
        self.instance = instance
        self.kind = kind

    def get(self, key):
        conversation = self.instance.conversation(key)
        if conversation.kind != self.kind:
            raise TargetMismatch(f"Expected {self.kind}, got {conversation.kind}")
        return conversation

    def configured(self):
        self.instance._assert_open()
        return tuple(
            self.get(t) for t in self.instance._client.targets if t.kind == self.kind
        )

    def list(self):
        self.instance._assert_open()
        return self.instance._client.contacts(
            "groups" if self.kind == "group" else "friends"
        )


class Conversation:
    def __init__(self, instance: QQInstance, target: Target):
        self._instance = instance
        self._target = target

    @property
    def instance(self):
        return self._instance

    @property
    def target(self):
        return self._target

    @property
    def id(self):
        return self.target.id

    @property
    def name(self):
        return self.target.name

    @property
    def kind(self):
        return self.target.kind

    def _client(self):
        self.instance._assert_open()
        # Also reject a manually constructed handle outside this registry.
        if self.instance._client.target(self.target.id) != self.target:
            raise TargetMismatch("Conversation does not match the instance registry")
        return self.instance._client

    def open(self, *, route="auto") -> ConversationInfo:
        return self._client().open_chat(self.target, route=route)

    def open_detached(self):
        return self._client().open_detached(self.target)

    def detached_messages(self, *, refresh=False):
        options = {"refresh": True} if refresh else {}
        if type(refresh) is not bool:
            raise ValueError("refresh must be a boolean")
        return [
            MessageHandle(self, m.id, data=m)
            for m in self._client().read_detached(self.target, **options)
        ]

    def info(self) -> ConversationInfo:
        return self._client().conversation_info(self.target)

    def history_calendar(self, year, month):
        """Read enabled date controls after native year/month navigation."""
        from .ui.workflows.history import HistoryUI

        return HistoryUI(self._client()).calendar(self.target, year, month)

    def locate_history_image(self, on_date, *, expected_message_id):
        """Locate a unique private-history image and verify its known UI key."""
        from .ui.workflows.history import HistoryUI

        return HistoryUI(self._client()).locate_image(
            self.target, on_date, expected_message_id=expected_message_id
        )

    def favorite_stickers(self):
        from .ui.workflows.stickers import StickersUI

        return StickersUI(self._client()).list(self.target)

    @contextmanager
    def sticker_collection(self):
        """Keep one collection panel and UI transaction alive for unnamed items."""
        from .ui.workflows.stickers import StickersUI

        ui = StickersUI(self._client())
        with ui.panel(self.target):
            collection = FavoriteStickerCollection(self, ui)
            try:
                yield collection
            finally:
                collection._active = False

    def import_stickers(self, paths):
        """Confirm the import count and return the current collection and identities."""
        from .ui.workflows.stickers import StickersUI

        return StickersUI(self._client()).import_images(self.target, paths)

    def move_sticker_first(self, selector):
        from .ui.workflows.stickers import StickersUI

        return StickersUI(self._client()).move_first(self.target, selector)

    def sticker_remark(self, selector):
        from .ui.workflows.stickers import StickersUI

        return StickersUI(self._client()).remark(self.target, selector)

    def set_sticker_remark(self, selector, value):
        from .ui.workflows.stickers import StickersUI

        return StickersUI(self._client()).remark(self.target, selector, value)

    def delete_sticker(self, selector):
        from .ui.workflows.stickers import StickersUI

        return StickersUI(self._client()).delete(self.target, selector)

    def controls(self, *, area="chat"):
        return self._client().control_ui.controls(self.target, area=area)

    def menus(self, area):
        return self._client().control_ui.conversation_menu(self.target, area)

    def action_plan(self, action, *, area="settings"):
        return self._client().control_ui.plan_control(
            self.target, action, area=area
        )

    def settings(self):
        return self._client().control_ui.settings(self.target)

    def set_pinned(self, enabled):
        return self._client().control_ui.set_pinned(self.target, enabled)

    def set_muted(self, enabled):
        return self._client().control_ui.set_muted(self.target, enabled)

    def draft(self):
        return self._client().control_ui.draft(self.target)

    def set_draft(self, text):
        return self._client().control_ui.set_draft(self.target, text)

    def clear_draft(self, *, expected_text):
        return self._client().control_ui.clear_draft(
            self.target, expected_text=expected_text
        )

    def send_draft(self, *, expected_text):
        receipt = self._client().control_ui.send_draft(
            self.target, expected_text=expected_text
        )
        return MessageHandle(self, receipt.message_id, receipt=receipt)

    def send_text(self, text: str, *, timeout=10) -> MessageHandle:
        receipt = self._client().send_text(self.target, text, timeout=timeout)
        return MessageHandle(self, receipt.message_id, receipt=receipt)

    def mark_unread(self):
        return self._client().control_ui.mark_unread(self.target)

    def send_face(self, label, *, text="") -> MessageHandle:
        receipt = self._client().control_ui.send_face(
            self.target, label, text=text
        )
        return MessageHandle(self, receipt.message_id, receipt=receipt)

    def send_file(self, path, *, timeout=30) -> MessageHandle:
        receipt = self._client().send_file(self.target, path, timeout=timeout)
        return MessageHandle(self, receipt.message_id, receipt=receipt)

    def send_image(self, path, *, timeout=30) -> MessageHandle:
        receipt = self._client().send_image(self.target, path, timeout=timeout)
        return MessageHandle(self, receipt.message_id, receipt=receipt)

    def messages(self) -> list[MessageHandle]:
        return [
            MessageHandle(self, m.id, data=m)
            for m in self._client().read_messages(self.target)
        ]

    def get_message(self, message_id: str) -> MessageHandle:
        data = self._client().get_message(self.target, message_id)
        return MessageHandle(self, data.id, data=data)

    def history(
        self,
        *,
        query=None,
        pages=1,
        limit=100,
        category="all",
        sender=None,
        on_date=None,
    ) -> list[HistoryRecord]:
        filters = {
            k: v for k, v in (("sender", sender), ("on_date", on_date)) if v is not None
        }
        return self._client().history(
            self.target,
            query=query,
            pages=pages,
            limit=limit,
            category=category,
            **filters,
        )

    def watch(self, **kwargs):
        yield from self.instance.events([self], **kwargs)

    def wait_for_message(self, *, timeout=30, predicate=None, on_ready=None):
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        for event in self.watch(duration=timeout, on_ready=on_ready):
            if predicate is None or predicate(event):
                return event
        raise TimeoutError(f"No matching message in {self.id} within {timeout}s")


class Group(Conversation):
    def delete_files(self, names, *, expected=None):
        from .ui.workflows.group_files import GroupFilesUI

        return GroupFilesUI(self._client()).delete_files(self.target, names, expected=expected)

    def folder_properties(self, name, *, expected=None):
        from .ui.workflows.group_files import GroupFilesUI

        return GroupFilesUI(self._client()).folder_properties(
            self.target, name, expected=expected
        )

    def download_files(self, names, destination, *, expected=None):
        from .ui.workflows.group_files import GroupFilesUI

        return GroupFilesUI(self._client()).download_files(
            self.target, names, destination, expected=expected
        )

    def download_folder(self, name, download_root, *, expected=None):
        from .ui.workflows.group_files import GroupFilesUI

        return GroupFilesUI(self._client()).download_folder(
            self.target, name, download_root, expected=expected
        )

    def save_qr(self, destination):
        from .ui.workflows.sharing import SharingUI

        return SharingUI(self._client()).save_qr(self.target, destination)

    def share_qr_to(self, destination):
        from .ui.workflows.sharing import SharingUI

        scope = self.instance.conversation(destination)
        return SharingUI(self._client()).share(self.target, scope.target, kind="qr")

    def share_link(self):
        """Return QQ's group URL, replacing the automation session's clipboard."""
        return self._client().control_ui.group_share_link(self.target)

    def tags(self):
        return self._client().control_ui.group_tags(self.target)

    def set_tag(self, label, enabled=True):
        return self._client().control_ui.set_group_tag(self.target, label, enabled)

    def join_policy(self):
        return self._client().control_ui.group_join_policy(self.target)

    def description(self):
        return self._client().control_ui.group_description(self.target)

    def set_description(self, value):
        return self._client().control_ui.set_group_description(self.target, value)

    def publish_notice(self, text, *, image=None):
        from .ui.workflows.notices import NoticesUI

        return NoticesUI(self._client()).publish(self.target, text, image=image)

    def edit_notice(self, text, new_text, *, image=None, remove_image=False, expected=None):
        """Edit text, preserving the image unless replaced or explicitly removed."""
        from .ui.workflows.notices import NoticesUI

        return NoticesUI(self._client()).edit(
            self.target,
            text,
            new_text,
            image=image,
            remove_image=remove_image,
            expected=expected,
        )

    def delete_notice(self, text, *, expected=None):
        from .ui.workflows.notices import NoticesUI

        return NoticesUI(self._client()).delete(self.target, text, expected=expected)

    def notice_pinned(self, text, *, expected=None):
        from .ui.workflows.notices import NoticesUI

        return NoticesUI(self._client()).pinned(self.target, text, expected=expected)

    def set_notice_pinned(self, text, enabled, *, expected=None):
        from .ui.workflows.notices import NoticesUI

        return NoticesUI(self._client()).set_pinned(self.target, text, enabled, expected=expected)

    def set_join_mode(self, value):
        return self._client().control_ui.set_group_join_policy(
            self.target, "join_mode", value
        )

    def set_member_invites(self, value):
        return self._client().control_ui.set_group_join_policy(
            self.target, "member_invites", value
        )

    def set_invite_approval(self, value):
        return self._client().control_ui.set_group_join_policy(
            self.target, "invite_approval", value
        )

    def set_bot_invites(self, value):
        return self._client().control_ui.set_group_join_policy(
            self.target, "bot_invites", value
        )

    def set_bot_invite_approval(self, value):
        return self._client().control_ui.set_group_join_policy(
            self.target, "bot_invite_approval", value
        )

    def preferences(self):
        return self._client().control_ui.group_preferences(self.target)

    def set_whole_ban(self, enabled):
        return self._client().control_ui.set_whole_ban(self.target, enabled)

    def set_speak_limit(self, per_minute):
        return self._client().control_ui.set_speak_limit(self.target, per_minute)

    def set_message_mode(self, mode):
        return self._client().control_ui.set_message_mode(self.target, mode)

    def leave(self):
        return self._client().control_ui.leave(self.target)

    def kick_member(self, name, *, expected_user_id):
        return self._client().control_ui.kick_member(
            self.target, name, expected_user_id=expected_user_id
        )

    def set_member_ban(self, name, duration):
        """Mute/unmute one unique loaded display name; no QQ ID is inferred."""
        return self._client().control_ui.set_member_ban(
            self.target, name, duration
        )

    def set_member_admin(self, name, enabled, *, expected_user_id):
        """Change one member's role after fresh numeric QQ profile verification."""
        return self._client().control_ui.set_member_admin(
            self.target, name, enabled, expected_user_id=expected_user_id
        )

    def application_menu(self):
        return self._client().control_ui.application_menu(self.target)

    def member_submenu(self, member, entry):
        return self._client().control_ui.member_submenu(self.target, member, entry)

    def files(self, *, query=None, order=None):
        """Read loaded UI rows; row ordinals are not server file IDs."""
        options = {"query": query}
        if order is not None:
            options["order"] = order
        return self._client().control_ui.group_files(self.target, **options)

    def file_actions(self, row_index, *, query=None):
        return self._client().control_ui.file_actions(
            self.target, row_index, query=query
        )

    def essences(self):
        return self._client().control_ui.group_essences(self.target)

    def notices(self):
        return self._client().control_ui.group_notices(self.target)

    def file_entries(self, *, folder=None):
        """Read loaded root or one exact child folder, restoring the root after reading."""
        return self._client().control_ui.group_file_entries(
            self.target, folder=folder
        )

    def create_folder(self, name):
        return self._client().control_ui.create_group_folder(self.target, name)

    def rename_folder(self, name, new_name, *, expected=None):
        return self._client().control_ui.rename_group_folder(
            self.target, name, new_name, expected=expected
        )

    def delete_folder(self, name, *, expected=None):
        """Delete an empty root folder selected by its exact, unique name."""
        return self._client().control_ui.delete_group_folder(
            self.target, name, expected=expected
        )

    def upload_file(self, path):
        """Upload directly to group files through the native picker."""
        return self._client().control_ui.upload_group_file(self.target, path)

    def move_file(self, name, folder, *, source_folder=None, expected=None):
        return self._client().control_ui.move_group_file(
            self.target, name, folder, source_folder=source_folder, expected=expected
        )

    def rename_file(self, name, new_name, *, expected=None):
        return self._client().control_ui.rename_group_file(
            self.target, name, new_name, expected=expected
        )

    def delete_file(self, name, *, expected=None):
        return self._client().control_ui.delete_group_file(
            self.target, name, expected=expected
        )

    def download_file(self, name, destination, *, expected=None):
        """Save to a new local path and return the resulting size and SHA-256."""
        return self._client().control_ui.download_group_file(
            self.target, name, destination, expected=expected
        )

    def file_sort_options(self):
        return self._client().control_ui.file_sort_options(self.target)

    def file_sort_state(self):
        return self._client().control_ui.file_sort_state(self.target)

    def members(self, *, query=None, resolve_ids=False) -> list[GroupMemberInfo]:
        return self._client().member_details(
            self.target, query=query, resolve_ids=resolve_ids
        )

    def member_profile(self, name):
        return self._client().control_ui.member_profile(self.target, name)

    def member_actions(self, name):
        return self._client().control_ui.member_actions(self.target, name)

    def set_own_card(self, value):
        return self._client().control_ui.set_own_card(self.target, value)

    def poke_member(self, name, *, expected_user_id):
        return self._client().control_ui.poke_member(
            self.target, name, expected_user_id=expected_user_id
        )

    def set_member_card(self, name, value, *, expected_user_id):
        return self._client().control_ui.set_member_card(
            self.target, name, value, expected_user_id=expected_user_id
        )

    def send_mention(self, member, text):
        receipt = self._client().control_ui.send_mention(
            self.target, str(member), text
        )
        return MessageHandle(self, receipt.message_id, receipt=receipt)

    def member(self, name: str) -> GroupMemberInfo:
        matches = [m for m in self.members() if m.name == name]
        if len(matches) != 1:
            raise TargetMismatch(
                f"Expected one loaded member named {name!r}, found {len(matches)}"
            )
        return matches[0]


class Friend(Conversation):
    def profile(self, *, full=False):
        from .ui.workflows.sharing import SharingUI

        return SharingUI(self._client()).profile(self.target, full=full)

    def like(self):
        """Submit one profile like, then read and reopen its visible count."""
        from .ui.workflows.sharing import SharingUI

        return SharingUI(self._client()).like(self.target)

    def share_to(self, destination):
        from .ui.workflows.sharing import SharingUI

        scope = self.instance.conversation(destination)
        return SharingUI(self._client()).share(
            self.target, scope.target, kind="contact"
        )

    def poke(self):
        """Return the new system Message after a verified native poke."""
        return self._client().control_ui.poke_friend(self.target)

    def auto_download(self):
        return self._client().control_ui.auto_download(self.target)

    def set_auto_download(self, enabled):
        return self._client().control_ui.set_auto_download(self.target, enabled)

    def set_blocked(self, enabled):
        return self._client().control_ui.set_blocked(self.target, enabled)


class FavoriteStickerCollection:
    """Short-lived collection owned by Conversation.sticker_collection()."""

    def __init__(self, conversation, ui):
        self.conversation, self._ui, self._active = conversation, ui, True

    def _check(self):
        self.conversation._client()
        if not self._active:
            raise InstanceClosed("Sticker collection scope is closed")

    def items(self):
        self._check()
        return self._ui.list(self.conversation.target)

    def move_first(self, selector):
        self._check()
        return self._ui.move_first(self.conversation.target, selector)

    def remark(self, selector):
        self._check()
        return self._ui.remark(self.conversation.target, selector)

    def set_remark(self, selector, value):
        self._check()
        return self._ui.remark(self.conversation.target, selector, value)

    def delete(self, selector):
        self._check()
        return self._ui.delete(self.conversation.target, selector)


class MessageHandle:
    def __init__(
        self,
        conversation: Conversation,
        message_id: str,
        *,
        data: Message | None = None,
        receipt=None,
    ):
        if data is not None and (
            data.id != str(message_id) or data.conversation_id != conversation.id
        ):
            raise TargetMismatch("Message snapshot belongs to another conversation")
        self._conversation = conversation
        self._id = str(message_id)
        self._data = data
        self._receipt = receipt

    @property
    def conversation(self):
        return self._conversation

    @property
    def instance(self):
        return self.conversation.instance

    @property
    def id(self):
        return self._id

    @property
    def receipt(self):
        return self._receipt

    @property
    def data(self):
        self.instance._assert_open()
        return self._data if self._data is not None else self.refresh()

    @property
    def text(self):
        return self.data.text

    def refresh(self) -> Message:
        self._data = self.conversation._client().get_message(
            self.conversation.target, self.id
        )
        return self._data

    def reply(self, text: str, *, timeout=10) -> MessageHandle:
        receipt = self.conversation._client().reply_text(
            self.conversation.target, self.id, text, timeout=timeout
        )
        return MessageHandle(self.conversation, receipt.message_id, receipt=receipt)

    def actions(self):
        return self.conversation._client().control_ui.message_actions(
            self.conversation.target, self.id
        )

    def contact_profile(self, *, expected_user_id):
        from .ui.workflows.sharing import SharingUI

        return SharingUI(self.conversation._client()).contact_profile(
            self.conversation.target, self.id, expected_user_id=expected_user_id
        )

    def secondary_controls(self):
        return self.conversation._client().control_ui.message_secondary(
            self.conversation.target, self.id
        )

    def essence_state(self):
        return self.conversation._client().control_ui.message_essence_state(
            self.conversation.target, self.id
        )

    def set_essence(self, enabled):
        return self.conversation._client().control_ui.set_message_essence(
            self.conversation.target, self.id, enabled
        )

    def recall(self, *, expected_text=None):
        return self.conversation._client().control_ui.recall_message(
            self.conversation.target, self.id, expected_text=expected_text
        )

    def reaction_palette(self):
        """Inspect choices; does not submit a reaction or infer protocol IDs."""
        return self.conversation._client().control_ui.reaction_palette(
            self.conversation.target, self.id
        )

    def reactions(self):
        """Read loaded reaction chips; unnamed emoji remain unnamed."""
        return self.conversation._client().control_ui.message_reactions(
            self.conversation.target, self.id
        )

    def add_reaction(self, label, *, expected_text=None):
        """Add a named quick reaction only when this account has none on the message."""
        return self.conversation._client().control_ui.add_reaction(
            self.conversation.target, self.id, label, expected_text=expected_text
        )

    def remove_own_reaction(self, *, expected_text=None):
        """Remove this account's unique selected chip; never another user's reaction."""
        return self.conversation._client().control_ui.remove_own_reaction(
            self.conversation.target, self.id, expected_text=expected_text
        )

    def plan(self, action):
        return self.conversation._client().control_ui.plan_message_action(
            self.conversation.target, self.id, action
        )

    def forward_to(self, destination):
        scope = self.instance.conversation(destination)
        receipt = self.conversation._client().control_ui.forward(
            self.conversation.target, self.id, scope.target
        )
        return MessageHandle(scope, receipt.message_id, receipt=receipt)


@dataclass(frozen=True)
class MessageEvent:
    conversation: Conversation
    message: MessageHandle

    def __post_init__(self):
        if self.message.conversation is not self.conversation:
            raise TargetMismatch("Event message belongs to another conversation")

    @property
    def instance(self):
        return self.conversation.instance

    @property
    def text(self):
        return self.message.text

    @property
    def sender(self):
        return self.message.data.sender

    def reply(self, text, *, quote=False):
        return self.message.reply(text) if quote else self.conversation.send_text(text)


@dataclass(eq=False)
class Subscription:
    instance: QQInstance
    handler: object
    conversation: Conversation | None
    active: bool = True

    def cancel(self):
        self.active = False
        if self in self.instance._subscriptions:
            self.instance._subscriptions.remove(self)
