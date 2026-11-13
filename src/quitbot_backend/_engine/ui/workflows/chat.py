"""Scoped QQ controls. No arbitrary click/invoke is exposed to remote callers."""

import hashlib
import re
import time
from contextlib import contextmanager
from dataclasses import replace

from quitbot_backend._engine.discovery import profile_qq_id
from quitbot_backend._engine.domain.controls import (
    ActionPlan,
    ChatSettings,
    ControlInfo,
    DraftState,
    ForwardReceipt,
    MemberProfile,
    MenuAction,
    SettingReceipt,
)
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    DraftNotEmpty,
    SendOutcomeUnknown,
    TargetMismatch,
)
from quitbot_backend._engine.locking import ui_transaction
from quitbot_backend._engine.models import (
    GroupMemberInfo,
    MentionReceipt,
    SendReceipt,
    editor_has_content,
    editor_text,
    text_of,
)
from quitbot_backend._engine.operations import MessageOperations, validate_text
from quitbot_backend._engine.ui.backend import wait_until

from .inspection import DesktopLocked, DesktopUnavailable, require_unlocked


def action_risk(name):
    if any(word in name for word in ("锁定", "退出账号")):
        return "account_session"
    if any(
        word in name
        for word in ("删除", "退出群", "踢出", "撤回", "解散", "移除", "移出本群")
    ):
        return "destructive"
    if any(word in name for word in ("屏蔽", "举报", "隐私")):
        return "privacy"
    if any(word in name for word in ("管理员", "禁言", "修改群昵称", "设为精华")):
        return "moderation"
    if "免打扰" in name or "群消息设置" in name:
        return "notification"
    if any(word in name for word in ("红包", "转账")):
        return "payment"
    if any(word in name for word in ("通话", "共享", "远程", "语音", "截图")):
        return "capture_or_call"
    if any(
        word in name
        for word in (
            "发送",
            "转发",
            "戳一戳",
            "TA",
            "收藏",
            "邀请",
            "分享",
            "窗口抖动",
            "本群昵称",
            "重命名",
            "移动",
            "标记未读",
            "创建群聊",
            "加好友/群",
            "闪传",
        )
    ):
        return "external_write"
    if "另存为" in name:
        return "local_file_write"
    return "local_or_read"


def control_label(control):
    if control.Name:
        return control.Name
    if control.ControlTypeName == "GroupControl":
        texts = [
            c.Name
            for c in control.GetChildren()
            if c.ControlTypeName == "TextControl" and c.Name
        ]
        return texts[0] if len(texts) == 1 else None
    if control.ControlTypeName in ("ButtonControl", "EditControl"):
        texts = [
            c.Name
            for c in control.GetParentControl().GetChildren()
            if c.ControlTypeName == "TextControl" and c.Name
        ]
        return texts[0] if texts else None
    return None


def draft_state(node):
    nodes = tuple(node.walk())
    return DraftState(
        editor_text(node),
        any(n.has_class("editor-el__reply-wrapper") for n in nodes),
        any(
            n.has_class("editor-el--inline-block") and not n.has_class("editor-el__at")
            for n in nodes
        ),
        tuple(text_of(n) for n in nodes if n.has_class("editor-el__at")),
    )


def forwarding_label(node, kind):
    content = next((x for x in node.walk() if x.has_class("list-item__content")), None)
    if content is None:
        return node.name
    value = "".join(
        text_of(child)
        for child in content.children
        if not child.has_class("list-item__summary")
    )
    return re.sub(r"\s*\(\d+人\)$", "", value) if kind == "group" else value


def checked_forwarding_labels(node, kind):
    return [
        forwarding_label(row, kind)
        for row in node.walk()
        if row.has_class("list-item")
        and any(
            child.has_class("q-checkbox") and child.has_class("is-checked")
            for child in row.walk()
        )
    ]


class ChatUI:
    def __init__(self, client):
        self.client = client
        self.b = client.backend
        self.identities = {}

    @property
    def navigation(self):
        if not hasattr(self, "_navigation"):
            from .navigation import NavigationUI

            self._navigation = NavigationUI(self.client)
        return self._navigation

    def global_menu(self, area):
        return self.navigation.global_menu(area)

    def conversation_menu(self, key, area):
        return self.navigation.conversation_menu(key, area)

    def set_member_ban(self, key, name, duration):
        from .moderation import ModerationUI

        return ModerationUI(self.client).set_member_ban(key, name, duration)

    def set_member_admin(self, key, name, enabled, *, expected_user_id):
        from .administration import AdministrationUI

        return AdministrationUI(self.client).set_member_admin(
            key, name, enabled, expected_user_id=expected_user_id
        )

    @property
    def preferences(self):
        from .preferences import PreferencesUI

        return PreferencesUI(self.client)

    def group_preferences(self, key):
        return self.preferences.group_preferences(key)

    def set_muted(self, key, enabled):
        return self.preferences.set_muted(key, enabled)

    @property
    def message_mutations(self):
        from .message_mutations import MessageMutationsUI

        return MessageMutationsUI(self.client)

    def message_essence_state(self, key, message_id):
        return self.message_mutations.essence_state(key, message_id)

    def set_message_essence(self, key, message_id, enabled):
        return self.message_mutations.set_essence(key, message_id, enabled)

    def recall_message(self, key, message_id, *, expected_text=None):
        return self.message_mutations.recall(
            key, message_id, expected_text=expected_text
        )

    def set_whole_ban(self, key, enabled):
        return self.preferences.set_whole_ban(key, enabled)

    def set_speak_limit(self, key, per_minute):
        return self.preferences.set_speak_limit(key, per_minute)

    def set_message_mode(self, key, mode):
        return self.preferences.set_message_mode(key, mode)

    def auto_download(self, key):
        return self.preferences.auto_download(key)

    def set_auto_download(self, key, enabled):
        return self.preferences.set_auto_download(key, enabled)

    def kick_member(self, key, name, *, expected_user_id):
        from .moderation import ModerationUI

        return ModerationUI(self.client).kick_member(
            key, name, expected_user_id=expected_user_id
        )

    def leave(self, key):
        from .moderation import ModerationUI

        return ModerationUI(self.client).leave(key)

    @property
    def secondary(self):
        if not hasattr(self, "_secondary"):
            from .secondary import SecondaryUI

            self._secondary = SecondaryUI(self.client)
        return self._secondary

    def application_menu(self, key):
        return self.secondary.application_menu(key)

    def member_submenu(self, key, member, entry):
        return self.secondary.member_submenu(key, member, entry)

    def group_files(self, key, *, query=None, order=None):
        return self.secondary.group_files(key, query=query, order=order)

    def file_actions(self, key, row_index, *, query=None):
        return self.secondary.file_actions(key, row_index, query=query)

    @property
    def file_management(self):
        from .group_files import GroupFilesUI

        return GroupFilesUI(self.client)

    def group_join_policy(self, key):
        from .group_settings import GroupSettingsUI

        return GroupSettingsUI(self).read(key)

    def set_group_join_policy(self, key, field, value):
        from .group_settings import GroupSettingsUI

        return GroupSettingsUI(self).set(key, field, value)

    def group_description(self, key):
        from .group_settings import GroupSettingsUI

        return GroupSettingsUI(self).description(key)

    def group_tags(self, key):
        from .group_settings import GroupSettingsUI

        return GroupSettingsUI(self).tags(key)

    def group_share_link(self, key):
        from .group_settings import GroupSettingsUI

        return GroupSettingsUI(self).share_link(key)

    def poke_friend(self, key):
        from .member_interactions import MemberInteractionsUI

        return MemberInteractionsUI(self.client).poke_friend(key)

    def set_group_tag(self, key, label, enabled):
        from .group_settings import GroupSettingsUI

        return GroupSettingsUI(self).set_tag(key, label, enabled)

    def set_group_description(self, key, value):
        from .group_settings import GroupSettingsUI

        return GroupSettingsUI(self).set_description(key, value)

    def publish_group_notice(self, key, text):
        from .notices import NoticesUI

        return NoticesUI(self.client).publish(key, text)

    def mark_unread(self, key):
        return self.navigation.mark_unread(key)

    def group_essences(self, key):
        from .essence import EssenceUI

        return EssenceUI(self).read(key)

    def send_face(self, key, label, *, text=""):
        from .faces import FacesUI

        return FacesUI(self).send(key, label, text=text)

    def group_notices(self, key):
        from .notices import NoticesUI

        return NoticesUI(self.client).read(key)

    def group_file_entries(self, key, *, folder=None):
        return self.file_management.file_entries(key, folder=folder)

    def create_group_folder(self, key, name):
        return self.file_management.create_folder(key, name)

    def rename_group_folder(self, key, name, new_name, *, expected=None):
        return self.file_management.rename_folder(
            key, name, new_name, expected=expected
        )

    def delete_group_folder(self, key, name, *, expected=None):
        return self.file_management.delete_folder(key, name, expected=expected)

    def upload_group_file(self, key, path):
        return self.file_management.upload_file(key, path)

    def move_group_file(self, key, name, folder, *, source_folder=None, expected=None):
        return self.file_management.move_file(
            key, name, folder, source_folder=source_folder, expected=expected
        )

    def rename_group_file(self, key, name, new_name, *, expected=None):
        return self.file_management.rename_file(key, name, new_name, expected=expected)

    def delete_group_file(self, key, name, *, expected=None):
        return self.file_management.delete_file(key, name, expected=expected)

    def download_group_file(self, key, name, destination, *, expected=None):
        return self.file_management.download_file(
            key, name, destination, expected=expected
        )

    def file_sort_options(self, key):
        return self.secondary.file_sort_options(key)

    def file_sort_state(self, key):
        return self.secondary.file_sort_state(key)

    def message_secondary(self, key, message_id):
        return self.secondary.message_secondary(key, message_id)

    def reaction_palette(self, key, message_id):
        return self.secondary.reaction_palette(key, message_id)

    @property
    def reactions_ui(self):
        from .reactions import ReactionsUI

        return ReactionsUI(self.client)

    def message_reactions(self, key, message_id):
        return self.reactions_ui.reactions(key, message_id)

    def add_reaction(self, key, message_id, label, *, expected_text=None):
        return self.reactions_ui.add_reaction(
            key, message_id, label, expected_text=expected_text
        )

    def remove_own_reaction(self, key, message_id, *, expected_text=None):
        return self.reactions_ui.remove_own_reaction(
            key, message_id, expected_text=expected_text
        )

    @contextmanager
    def checked(self, key):
        target = self.client.target(key)
        with ui_transaction():
            require_unlocked(self.b.auto)
            self.client._open(target)
            self.b.profile(target.id, target.name, target.kind)
            yield target
            if self.b.header().Name != target.name:
                raise TargetMismatch("Conversation changed during control operation")

    def by_class(self, name, root=None, required=False):
        return self.b.find(
            root if root is not None else self.b.window,
            lambda x: name in x.ClassName.split(),
            required,
        )

    def _more_button(self):
        return self.b.find(
            self.b.header().GetParentControl().GetParentControl(),
            lambda x: x.ControlTypeName == "ButtonControl" and x.Name == "更多",
        )

    def _close_owned_panel(self):
        try:
            require_unlocked(self.b.auto)
        except DesktopLocked:
            return
        # Never send Escape while an unrelated quote/media/text draft exists.
        if self.by_class("side-panel") is not None and not editor_has_content(
            self.b.snapshot(self.b.editor())
        ):
            button = self._more_button()
            if self._settings_panel() is not None:
                # This button toggles the settings panel; Escape from the
                # toolbar is ignored in QQ's own-card editing layout.
                self.b.invoke(button)
            else:
                # Profile closing can temporarily prevent editor focus. The
                # roster accepts Escape from its verified chat's toolbar.
                focused = self.b.focus_control(button, resolve=self._more_button)
                self.b.ensure_focus(focused)
                self.b.auto.SendKeys("{Esc}", waitTime=0.05)
            wait_until(lambda: self.by_class("side-panel"), lambda panel: panel is None)

    @contextmanager
    def more(self):
        panel = self._settings_panel()
        opened = panel is None
        if opened:
            source_name = self.b.header().Name
            self.b.invoke(self._more_button())
            try:
                panel = wait_until(self._settings_panel, timeout=1.2)
            except ControlUnavailable:
                require_unlocked(self.b.auto)
                if self.b.header().Name != source_name:
                    raise TargetMismatch(
                        "Conversation changed while opening settings"
                    ) from None
                button = self._more_button()
                if not button.IsKeyboardFocusable:
                    raise
                self.b.focus_control(button, resolve=self._more_button)
                self.b.auto.SendKeys("{Enter}", waitTime=0.1)
                panel = wait_until(self._settings_panel)
        try:
            yield panel
        finally:
            if (
                opened
                and self._settings_panel() is not None
                and self.by_class("group-member-list") is None
            ):
                self._close_owned_panel()

    def _settings_panel(self):
        panel = self.by_class("side-panel")
        return (
            panel
            if panel is not None and self.by_class("set-top", panel) is not None
            else None
        )

    def _roster_pane(self):
        panel = self.by_class("side-panel")
        return self.by_class("group-member-list", panel) if panel is not None else None

    @contextmanager
    def roster(self, query=None):
        source_name = self.b.header().Name
        pane = self._roster_pane()
        opened = pane is None
        if opened:
            with self.more() as panel:
                self.b.invoke(self.by_class("header-more", panel, required=True))
                pane = wait_until(self._roster_pane)
        search = self.b.find(
            self.by_class("side-panel", required=True),
            lambda x: x.ControlTypeName == "EditControl" and x.Name == "搜索",
            False,
        )
        old = None
        try:
            if query is not None:
                if (
                    not isinstance(query, str)
                    or len(query) > 100
                    or any(ord(c) < 32 for c in query)
                ):
                    raise ValueError("Invalid member query")
                if search is None:
                    raise ControlUnavailable("Member search is unavailable")
                pattern = search.GetValuePattern()
                old = pattern.Value
                pattern.SetValue(query, waitTime=0.3)
            if query is None:
                wait_until(self._members)
            yield pane
        finally:
            unlocked = True
            try:
                require_unlocked(self.b.auto)
            except (DesktopLocked, DesktopUnavailable):
                unlocked = False
            same_source = unlocked and self.b.header().Name == source_name
            if same_source and old is not None and self._roster_pane() is not None:
                live_search = self.b.find(
                    self.by_class("side-panel", required=True),
                    lambda x: x.ControlTypeName == "EditControl" and x.Name == "搜索",
                    False,
                )
                if (
                    live_search is not None
                    and live_search.GetValuePattern() is not None
                ):
                    live_search.GetValuePattern().SetValue(old, waitTime=0.1)
            if same_source and opened:
                self._close_owned_panel()

    def _rows(self):
        pane = self._roster_pane()
        if pane is None:
            raise ControlUnavailable("Member side panel unavailable")
        return [
            x for x in self.b.walk(pane) if "group-user--large" in x.ClassName.split()
        ]

    def _member_row(self, name):
        matches = []
        for row in self._rows():
            texts = [
                x.Name
                for x in self.b.walk(row)
                if x.ControlTypeName == "TextControl" and x.Name
            ]
            if texts and texts[0] == name:
                matches.append(row)
        if len(matches) != 1:
            raise TargetMismatch(
                f"Expected one loaded member named {name!r}, found {len(matches)}"
            )
        return matches[0]

    def _context(self, control, *, resolve=None):
        if "group-user--large" in control.ClassName.split():
            labels = [
                c.Name
                for c in self.b.walk(control)
                if c.ControlTypeName == "TextControl" and c.Name
            ]
            if not labels:
                raise ControlUnavailable("Member row has no identity label")
            name = labels[0]
            resolve = lambda: self._member_row(name)
        if hasattr(control, "show_context_menu"):
            self.b.window.SetActive(waitTime=0.05)
            (resolve() if resolve else control).show_context_menu()
        else:
            if not control.IsKeyboardFocusable:
                # QQ roster rows acquire their keyboard tab stop after selection.
                self.b.invoke(control)
                wait_until(
                    lambda: (resolve() if resolve else control).IsKeyboardFocusable,
                    timeout=2,
                )
            if resolve:
                control = resolve()
                self.b.focus_control(control, resolve=resolve)
            else:
                self.b.focus_control(control)
            self.b.auto.SendKeys("{Shift}{F10}", waitTime=0.05)
        previous = [None]

        def settled_menu():
            menu = self.b.find(
                self.b.window, lambda x: x.ControlTypeName == "MenuControl", False
            )
            if menu is None or menu.IsOffscreen:
                return None
            r = menu.BoundingRectangle
            bounds = (r.left, r.top, r.right, r.bottom)
            ready = bounds == previous[0] and r.width() > 0 and r.height() > 0
            previous[0] = bounds
            return menu if ready else None

        return wait_until(settled_menu)

    def _dismiss_menu(self):
        try:
            require_unlocked(self.b.auto)
        except DesktopLocked:
            return
        if (
            self.b.find(
                self.b.window, lambda x: x.ControlTypeName == "MenuControl", False
            )
            is not None
        ):
            self.b.auto.SendKeys("{Esc}", waitTime=0.05)

    def _menu_item(self, menu, name):
        return self.b.find(
            menu, lambda x: x.ControlTypeName == "MenuItemControl" and x.Name == name
        )

    def _profile(self, target, name, role):
        from .profile_cards import ProfileCards

        probe = ProfileCards(self.b)
        menu = self._context(self._member_row(name))
        try:
            self.b.invoke(self._menu_item(menu, "查看资料"))

            def labelled(fields):
                try:
                    return bool(profile_qq_id(fields))
                except ControlUnavailable:
                    return False

            result = wait_until(lambda: probe.refreshed(labelled), timeout=3)
            card = probe.reveal(result)
            try:
                fields = result[2]
                number = profile_qq_id(fields)
                self.identities[(target.id, name)] = number
                title = self.by_class("buddy-profile__header-name", card)
                label = title.Name if title is not None else ""
                nickname = (
                    label.removeprefix("查看").removesuffix("的个人主页")
                    if label.startswith("查看") and label.endswith("的个人主页")
                    else None
                )
                return MemberProfile(target.id, name, number, role, fields, nickname)
            finally:
                card.GetWindowPattern().Close(waitTime=0.3)
        finally:
            self._dismiss_menu()

    def _members(self):
        result = []
        for row in self._rows():
            texts = [
                x.Name
                for x in self.b.walk(row)
                if x.ControlTypeName == "TextControl" and x.Name
            ]
            if texts:
                result.append(
                    GroupMemberInfo(
                        texts[0],
                        "owner"
                        if "群主" in texts[1:]
                        else "admin"
                        if "管理员" in texts[1:]
                        else "member",
                    )
                )
        return result

    def members(self, key, *, query=None, resolve_ids=False):
        with self.checked(key) as target:
            if target.kind != "group":
                raise ValueError("Members require a group")
            with self.roster(query):
                result = self._members()
                if resolve_ids:
                    resolved = []
                    for member in result:
                        profile = self._profile(target, member.name, member.role)
                        resolved.append(
                            replace(
                                member,
                                user_id=profile.user_id,
                                nickname=profile.nickname,
                            )
                        )
                    result = resolved
                return result

    def member_profile(self, key, name):
        with self.checked(key) as target:
            if target.kind != "group":
                raise ValueError("Member profiles require a group")
            with self.roster(name):
                members = [m for m in self._members() if m.name == name]
                if len(members) != 1:
                    raise TargetMismatch("Member name is missing or ambiguous")
                return self._profile(target, name, members[0].role)

    def member_actions(self, key, name):
        with self.checked(key) as target:
            if target.kind != "group":
                raise ValueError("Member actions require a group")
            with self.roster(name):
                menu = self._context(self._member_row(name))
                try:
                    return self._actions(menu)
                finally:
                    self._dismiss_menu()

    def _actions(self, menu):
        return [
            MenuAction(x.Name, x.IsEnabled, action_risk(x.Name))
            for x in self.b.walk(menu)
            if x.ControlTypeName == "MenuItemControl"
        ]

    def message_menu(self, key, message_id):
        message = self.client.get_message(key, message_id)

        def content():
            item = self.b.find(
                self.b.window,
                lambda x: (
                    x.AutomationId == str(message_id) and "ml-item" in x.ClassName.split()
                ),
            )
            return self.by_class(
                "file-message--content"
                if message.kind == "file"
                else "msg-content-container",
                item,
                required=True,
            )

        current = content()
        if current.IsOffscreen:
            scroll = current.GetPattern(self.b.auto.PatternId.ScrollItemPattern)
            if scroll is None:
                raise ControlUnavailable("Message cannot be scrolled into view")
            scroll.ScrollIntoView(waitTime=0.1)
            current = wait_until(content, lambda c: not c.IsOffscreen)
        return self._context(current, resolve=content)

    def message_actions(self, key, message_id):
        with self.checked(key):
            menu = self.message_menu(key, message_id)
            try:
                return self._actions(menu)
            finally:
                self._dismiss_menu()

    def plan_message_action(self, key, message_id, action):
        actions = self.message_actions(key, message_id)
        match = next((a for a in actions if a.name == action), None)
        return ActionPlan(
            action,
            self.client.target(key).id,
            match is not None,
            match.enabled if match else False,
            False,
            "Inspection only; use a typed implemented operation to commit",
        )

    def draft(self, key):
        with self.checked(key):
            return draft_state(self.b.snapshot(self.b.editor()))

    def set_draft(self, key, text):
        validate_text(text)
        with self.checked(key):
            if editor_has_content(self.b.snapshot(self.b.editor())):
                raise DraftNotEmpty("Existing draft retained")
            editor = self.b.focus_editor()
            self.b.type_literal(editor, text)
            return wait_until(
                lambda: draft_state(self.b.snapshot(self.b.editor())),
                lambda state: state.text.strip("\n") == text.strip("\n"),
            )

    def clear_draft(self, key, *, expected_text):
        with self.checked(key):
            state = draft_state(self.b.snapshot(self.b.editor()))
            if (
                state.text != expected_text
                or state.has_quote
                or state.has_media
                or state.mentions
            ):
                raise DraftNotEmpty("Draft differs from expected plain text; retained")
            editor = self.b.focus_editor()
            self.b.ensure_focus(editor)
            self.b.auto.SendKeys("{Ctrl}a{Back}", waitTime=0.05)
            return wait_until(
                lambda: draft_state(self.b.snapshot(self.b.editor())),
                lambda s: (
                    not s.text.strip()
                    and not s.has_media
                    and not s.has_quote
                    and not s.mentions
                ),
            )

    def _commit_text(self, target, previous, text, *, timeout=10):
        if self.b.header().Name != target.name:
            raise TargetMismatch("Conversation changed before commit")
        self.b.ensure_focus(self.b.editor())
        try:
            self.b.invoke(
                wait_until(
                    lambda: self.b.by_class("send-msg"), lambda button: button.IsEnabled
                )
            )
            messages = wait_until(
                lambda: [
                    m
                    for m in self.client._read(target)
                    if m.id not in previous
                    and m.direction == "outgoing"
                    and m.text == text
                ],
                timeout=timeout,
            )
            wait_until(
                lambda: editor_has_content(self.b.snapshot(self.b.editor())),
                lambda content: not content,
                timeout=timeout,
            )
        except Exception as error:
            raise SendOutcomeUnknown(
                "Submission could not be verified; inspect QQ before retrying"
            ) from error
        return SendReceipt(target.id, messages[-1].id, text)

    def send_draft(self, key, *, expected_text):
        validate_text(expected_text)
        with self.checked(key) as target:
            state = draft_state(self.b.snapshot(self.b.editor()))
            if (
                state.text != expected_text
                or state.has_quote
                or state.has_media
                or state.mentions
            ):
                raise DraftNotEmpty("Draft differs from expected plain text; retained")
            previous = {m.id for m in self.client._read(target)}
            self.b.focus_editor()
            return self._commit_text(target, previous, expected_text)

    def send_mention(self, key, member, text):
        validate_text(text)
        if not isinstance(member, str) or not member:
            raise ValueError("Provide a member display name or QQ number")
        with self.checked(key) as target:
            if target.kind != "group":
                raise ValueError("Native mentions require a group")
            previous = MessageOperations(self.client)._prepare(target)
            with self.roster(None if member.isdigit() else member):
                entries = self._members()
                if member.isdigit():
                    aliases = {
                        t.name
                        for t in self.client.targets
                        if t.kind == "private" and t.id == member
                    }
                    candidates = [m for m in entries if m.name in aliases]
                    if len(candidates) != 1:
                        candidates = entries
                    matches = [
                        m
                        for m in candidates
                        if self._profile(target, m.name, m.role).user_id == member
                    ]
                else:
                    matches = [m for m in entries if m.name == member]
                    if len(matches) == 1:
                        self._profile(target, matches[0].name, matches[0].role)
                if len(matches) != 1:
                    raise TargetMismatch("Member identity is not uniquely loaded")
                menu = self._context(self._member_row(matches[0].name))
                try:
                    self.b.invoke(self._menu_item(menu, "TA"))
                finally:
                    self._dismiss_menu()
                initial = wait_until(
                    lambda: draft_state(self.b.snapshot(self.b.editor())),
                    lambda state: bool(state.mentions),
                )
                if (
                    initial.has_quote
                    or initial.has_media
                    or len(initial.mentions) != 1
                    or initial.text.replace(initial.mentions[0], "", 1).strip()
                ):
                    raise TargetMismatch("Unexpected native mention draft")
                if initial.mentions[0] != f"@{matches[0].name}":
                    raise TargetMismatch(
                        "Mention chip does not match the selected member"
                    )
                editor = self.b.focus_editor()
                self.b.type_literal(editor, text)
                expected = initial.text + text
                wait_until(
                    lambda: editor_text(self.b.snapshot(self.b.editor())),
                    lambda value: value == expected,
                )
                receipt = self._commit_text(target, previous, expected)
                return MentionReceipt(
                    receipt.target_id,
                    receipt.message_id,
                    receipt.text,
                    member_id=self.identities[(target.id, matches[0].name)],
                    member_name=matches[0].name,
                )

    def forward(self, key, message_id, destination):
        source = self.client.target(key)
        target = self.client.target(destination)
        with ui_transaction():
            self.client._open(target)
            self.b.profile(target.id, target.name, target.kind)
            if editor_has_content(self.b.snapshot(self.b.editor())):
                raise DraftNotEmpty("Destination has an existing draft; retained")
            previous = {m.id for m in self.client._read(target)}
            with self.checked(source):
                original = self.client.get_message(source, message_id)
                if original.kind == "card":
                    from .sharing import SharingUI

                    sent = SharingUI(self.client).share(
                        source, target, kind="card", message_id=original.id
                    )
                    return ForwardReceipt(source.id, original.id, target.id, sent.id)
                if (
                    original.kind not in ("text", "file")
                    or original.quote is not None
                    or any(part.kind != "text" for part in original.parts)
                    or original.kind == "file"
                    and (
                        len(original.attachments) != 1
                        or original.attachments[0].name != original.text
                        or not original.text
                    )
                ):
                    raise ControlUnavailable(
                        "Native forwarding requires plain text or one identified file"
                    )
                if self.by_class("q-dialog") is not None:
                    raise ControlUnavailable("Existing QQ dialog must be handled first")
                video_file = False
                if original.kind == "file":
                    row = self.b.find(
                        self.b.window,
                        lambda c: (
                            c.AutomationId == original.id
                            and "ml-item" in c.ClassName.split()
                        ),
                    )
                    video_file = self.by_class("msg-preview--video", row) is not None
                menu = self.message_menu(source, message_id)
                dialog = None
                submitted = False
                try:
                    self.b.invoke(self._menu_item(menu, "转发"))
                    dialog = wait_until(lambda: self.by_class("selector-dialog"))
                    abstract = self.by_class("msg-abstract", dialog)
                    if video_file:
                        abstract = wait_until(
                            lambda: self.by_class("msg-abstract", dialog)
                        )
                        snapshot = self.b.snapshot(abstract)
                        if (
                            sum(n.has_class("video") for n in snapshot.walk()) != 1
                            or sum(
                                n.has_class("video-play-icon") for n in snapshot.walk()
                            )
                            != 1
                            or text_of(snapshot)
                        ):
                            raise TargetMismatch(
                                "Expected a single native video thumbnail preview"
                            )
                    elif (
                        abstract is not None
                        and original.text
                        and original.text[:20] not in text_of(self.b.snapshot(abstract))
                    ):
                        raise TargetMismatch(
                            "Forward preview does not match the source"
                        )
                    search = self.b.find(
                        dialog,
                        lambda x: (
                            x.ControlTypeName == "EditControl" and x.Name == "搜索"
                        ),
                    )
                    search.GetValuePattern().SetValue(target.name, waitTime=0.3)

                    def results():
                        return [
                            x
                            for x in self.b.walk(dialog)
                            if "list-item" in x.ClassName.split()
                            and forwarding_label(self.b.snapshot(x), target.kind)
                            == target.name
                        ]

                    rows = wait_until(results)
                    if len(rows) != 1:
                        raise TargetMismatch("Ambiguous forwarding destination")
                    if checked_forwarding_labels(self.b.snapshot(dialog), target.kind):
                        raise TargetMismatch(
                            "Unexpected preselected forwarding destination"
                        )
                    self.b.invoke(rows[0])
                    selected = self.by_class("selected-list", dialog)
                    if selected is not None:
                        wait_until(
                            lambda: [
                                x.Name
                                for x in self.b.walk(selected)
                                if x.ControlTypeName == "TextControl" and x.Name
                            ],
                            lambda names: names == [target.name],
                        )
                    else:
                        wait_until(
                            lambda: checked_forwarding_labels(
                                self.b.snapshot(dialog), target.kind
                            ),
                            lambda names: names == [target.name],
                        )
                    if self.b.header().Name != source.name:
                        raise TargetMismatch(
                            "Source conversation changed before forwarding"
                        )
                    button = self.b.find(
                        dialog,
                        lambda x: (
                            x.ControlTypeName == "ButtonControl" and x.Name == "确定"
                        ),
                    )
                    try:
                        submitted = True
                        self.b.invoke(button)
                        wait_until(
                            lambda: self.by_class("selector-dialog"),
                            lambda value: value is None,
                        )
                        self.client._open(target)
                        sent = wait_until(
                            lambda: [
                                m
                                for m in self.client._read(target)
                                if m.id not in previous
                                and m.direction == "outgoing"
                                and m.text == original.text
                                and m.kind == original.kind
                                and (
                                    m.kind != "file"
                                    or any(
                                        a.name == original.text
                                        and "已发送" in (a.status or "")
                                        for a in m.attachments
                                    )
                                )
                            ]
                        )[-1]
                    except Exception as error:
                        raise SendOutcomeUnknown(
                            "Forward outcome uncertain; inspect QQ before retrying"
                        ) from error
                    self.client._open(source)
                    return ForwardReceipt(source.id, original.id, target.id, sent.id)
                finally:
                    if (
                        dialog is not None
                        and not submitted
                        and self.by_class("selector-dialog") is not None
                    ):
                        self.b.invoke(
                            self.b.find(
                                dialog,
                                lambda x: (
                                    x.ControlTypeName == "ButtonControl"
                                    and x.Name == "取消"
                                ),
                            )
                        )
                    self._dismiss_menu()

    def _switch(self, panel, row_class, label=None):
        row = (
            self.by_class(row_class, panel)
            if row_class
            else self.b.find(
                panel,
                lambda x: (
                    "q-list-item" in x.ClassName.split()
                    and any(c.Name == label for c in self.b.walk(x))
                ),
                False,
            )
        )
        return self.by_class("q-switch", row) if row is not None else None

    def _switch_value(self, control):
        if control is None:
            return None
        pattern = control.GetPattern(self.b.auto.PatternId.TogglePattern)
        return (
            bool(pattern.ToggleState)
            if pattern and pattern.ToggleState in (0, 1)
            else None
        )

    def _field(self, panel, name):
        return self.b.find(
            panel,
            lambda x: x.ControlTypeName == "EditControl" and x.Name == name,
            False,
        )

    def settings(self, key):
        with self.checked(key) as target, self.more() as panel:
            card = self._field(panel, "填写我的本群昵称")
            remark = self._field(panel, "填写备注")
            return ChatSettings(
                target.id,
                self._switch_value(self._switch(panel, "set-top")),
                self._switch_value(self._switch(panel, "set-disturb")),
                self._switch_value(self._switch(panel, None, "屏蔽此人")),
                card.GetValuePattern().Value if card else None,
                remark.GetValuePattern().Value if remark else None,
            )

    def set_pinned(self, key, enabled):
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be boolean")
        with self.checked(key) as target, self.more() as panel:
            control = self._switch(panel, "set-top")
            before = self._switch_value(control)
            if before is None:
                raise ControlUnavailable("Pin switch state unavailable")
            if before != enabled:
                control.GetPattern(self.b.auto.PatternId.TogglePattern).Toggle(
                    waitTime=0.1
                )
                wait_until(
                    lambda: self._switch_value(self._switch(panel, "set-top")),
                    lambda v: v is enabled,
                )
            return SettingReceipt(target.id, "pinned", before, enabled)

    def set_blocked(self, key, enabled):
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be boolean")
        with self.checked(key) as target:
            if target.kind != "private":
                raise ValueError("Blocking requires a private conversation")
            if self.by_class("q-dialog") is not None:
                raise ControlUnavailable(
                    "An existing dialog must be handled before blocking"
                )
            with self.more() as panel:
                switch = self._switch(panel, None, "屏蔽此人")
                before = self._switch_value(switch)
                if before is None:
                    raise ControlUnavailable("Block state unavailable")
                if before != enabled:
                    switch.GetPattern(self.b.auto.PatternId.TogglePattern).Toggle(
                        waitTime=0.1
                    )
                    if self.by_class("q-dialog") is not None:
                        raise ControlUnavailable(
                            "Unexpected blocking confirmation; no generic dialog submission"
                        )
                    wait_until(
                        lambda: self._switch_value(
                            self._switch(panel, None, "屏蔽此人")
                        ),
                        lambda value: value is enabled,
                    )
            if self.settings(key).blocked is not enabled:
                raise ControlUnavailable("Block setting was not confirmed on reread")
            return SettingReceipt(target.id, "blocked", before, enabled)

    def poke_member(self, key, name, *, expected_user_id):
        from .member_interactions import MemberInteractionsUI

        return MemberInteractionsUI(self.client).poke_member(
            key, name, expected_user_id=expected_user_id
        )

    def set_member_card(self, key, name, value, *, expected_user_id):
        from .member_interactions import MemberInteractionsUI

        return MemberInteractionsUI(self.client).set_member_card(
            key, name, value, expected_user_id=expected_user_id
        )

    def set_own_card(self, key, value):
        from .moderation import mutation_guard

        if (
            not isinstance(value, str)
            or len(value) > 60
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("Card must be at most 60 characters without controls")
        submitted = [False]
        with mutation_guard(submitted), self.checked(key) as target:
            if target.kind != "group":
                raise ValueError("Own card requires a group")
            if editor_has_content(self.b.snapshot(self.b.editor())):
                raise DraftNotEmpty("Existing composer draft retained")
            with self.more() as panel:
                field = self._field(panel, "填写我的本群昵称")
                if field is None:
                    raise ControlUnavailable("Own group card editor unavailable")
                pattern = field.GetValuePattern()
                before = pattern.Value
                if before != value:
                    self.b.focus_control(field)
                    pattern.SetValue(value, waitTime=0.1)
                    self.b.ensure_focus(field)
                    submitted[0] = True
                    self.b.auto.SendKeys("{Enter}", waitTime=0.1)
                    self.b.focus_editor()  # QQ commits this field on blur.
                    wait_until(
                        lambda: field.GetValuePattern().Value,
                        lambda current: current == value
                        or (not value and current == self.b.account),
                    )
            self._close_owned_panel()
            with self.more():
                time.sleep(0.5)

                def current_card():
                    panel = self._settings_panel()
                    field = (
                        self._field(panel, "填写我的本群昵称")
                        if panel is not None
                        else None
                    )
                    return field.GetValuePattern().Value if field is not None else None

                # Clearing the card restores the account nickname in QQ's field.
                after = wait_until(
                    current_card,
                    lambda current: current == value
                    or (not value and current == self.b.account),
                )
            self.identities = {
                k: v for k, v in self.identities.items() if k[0] != target.id
            }
            return SettingReceipt(target.id, "own_card", before, after)

    def controls(self, key, *, area="chat"):
        if area not in ("chat", "settings", "members"):
            raise ValueError("area must be chat/settings/members")
        with self.checked(key) as target:
            if area == "settings":
                with self.more() as panel:
                    return self._catalog(panel)
            if area == "members":
                if target.kind != "group":
                    raise ValueError("Member controls require a group")
                with self.roster():
                    return self._catalog(self.by_class("side-panel", required=True))
            return self._catalog(self.b.window)

    def plan_control(self, key, action, *, area="settings"):
        matches = [
            c for c in self.controls(key, area=area) if action in (c.name, c.label)
        ]
        return ActionPlan(
            action,
            self.client.target(key).id,
            bool(matches),
            len(matches) == 1 and matches[0].enabled,
            False,
            "Read-only plan; destructive, privacy, payment and call controls have no generic commit endpoint",
        )

    def _catalog(self, root):
        result = []
        for control in self.b.walk(root):
            if control.ControlTypeName not in {
                "ButtonControl",
                "EditControl",
                "MenuItemControl",
                "CheckBoxControl",
                "TabItemControl",
                "ComboBoxControl",
                "RadioButtonControl",
                "ToolBarControl",
                "GroupControl",
            }:
                continue
            patterns = tuple(
                name
                for name in (
                    "Invoke",
                    "SelectionItem",
                    "Toggle",
                    "Value",
                    "ExpandCollapse",
                    "Scroll",
                    "Window",
                )
                if control.GetPattern(getattr(self.b.auto.PatternId, f"{name}Pattern"))
            )
            legacy = control.GetLegacyIAccessiblePattern()
            if legacy is not None and legacy.DefaultAction:
                patterns += ("LegacyIAccessible",)
            if (
                control.ControlTypeName == "GroupControl"
                and not patterns
                and not control.IsKeyboardFocusable
            ):
                continue
            label = control_label(control)
            key = hashlib.sha256(repr(control.GetRuntimeId()).encode()).hexdigest()[:16]
            result.append(
                ControlInfo(
                    key,
                    control.ControlTypeName,
                    control.Name,
                    control.ClassName,
                    control.AutomationId,
                    control.IsEnabled,
                    control.IsKeyboardFocusable,
                    patterns,
                    action_risk(label or control.Name),
                    label,
                )
            )
        return result
