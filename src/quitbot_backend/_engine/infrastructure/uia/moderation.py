"""Native preset/custom member mute, with explicit state readback."""

from contextlib import ExitStack, contextmanager

from quitbot_backend._engine.domain.controls import (
    GroupLeaveReceipt,
    MemberBanReceipt,
    MemberRemovalReceipt,
)
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    MutationOutcomeUnknown,
    TargetMismatch,
)
from quitbot_backend._engine.locking import ui_transaction
from quitbot_backend._engine.models import text_of
from quitbot_backend._engine.uia import wait_until

from .inspection import hover_control

PRESETS = {600: "10分钟", 3600: "1小时", 43200: "12小时", 86400: "1天"}


@contextmanager
def mutation_guard(submitted):
    try:
        yield
    except MutationOutcomeUnknown:
        raise
    except Exception as error:
        if submitted[0]:
            raise MutationOutcomeUnknown(
                "UI mutation submitted; inspect QQ before retrying"
            ) from error
        raise


def ban_state(actions):
    names = {a.name for a in actions if a.enabled}
    if "解除禁言" in names:
        return True
    if "设置群内禁言" in names:
        return False
    raise ControlUnavailable("Mute state/permission is not exposed in the member menu")


class ModerationUI:
    def __init__(self, client):
        self.ui = client.control_ui
        self.b = client.backend

    def leave(self, key):
        """Submit native leave only; never dissolve, and do not claim roster proof."""
        submitted = [False]
        with ui_transaction(), mutation_guard(submitted):
            with self.ui.checked(key) as target:
                if target.kind != "group":
                    raise ValueError("Leave requires a group")
                if self.ui.by_class("q-dialog-main") is not None:
                    raise ControlUnavailable("Close the existing dialog before leaving")
                panel = self.ui._settings_panel()
                if panel is None:
                    self.b.invoke(self.ui._more_button())
                    panel = wait_until(self.ui._settings_panel)
                exit_button = self.b.find(
                    panel,
                    lambda x: (
                        x.ControlTypeName == "ButtonControl" and x.Name == "退出群聊"
                    ),
                )
                self.b.invoke(exit_button)
                dialog = wait_until(lambda: self.ui.by_class("q-dialog-main"))
                title = self.ui.by_class("q-dialog-title", dialog, required=True)
                content = self.ui.by_class("q-dialog-content", dialog, required=True)
                if (
                    text_of(self.b.snapshot(title)) != "退出群聊"
                    or text_of(self.b.snapshot(content))
                    != "退出后不会通知群聊中其他成员，且不会再接收此群消息。"
                ):
                    raise ControlUnavailable(
                        "Unrecognized leave confirmation; dissolution is unsupported"
                    )
            confirm = self.b.find(
                dialog,
                lambda x: x.ControlTypeName == "ButtonControl" and x.Name == "确定",
            )
            submitted[0] = True
            self.b.invoke(confirm)
            wait_until(lambda: self.ui.by_class("q-dialog-main"), lambda x: x is None)
            return GroupLeaveReceipt(target.id, True)

    def kick_member(self, key, name, *, expected_user_id):
        """Verify a labelled QQ identity before confirming native member removal."""
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 100
            or any(ord(c) < 32 for c in name)
        ):
            raise ValueError("Provide an exact member display name")
        if (
            isinstance(expected_user_id, bool)
            or not str(expected_user_id).isdigit()
            or not 5 <= len(str(expected_user_id)) <= 12
        ):
            raise ValueError("An expected QQ number is required")
        number = str(expected_user_id)
        submitted = [False]
        with ui_transaction(), mutation_guard(submitted):
            with self.ui.checked(key) as target:
                if target.kind != "group":
                    raise ValueError("Member removal requires a group")
                if self.ui.by_class("q-dialog-main") is not None:
                    raise ControlUnavailable("Close the existing dialog before removal")
                profile = self.ui.member_profile(key, name)
                if profile.user_id != number:
                    raise TargetMismatch(
                        "Member QQ number differs from expected_user_id"
                    )
                if profile.role == "owner":
                    raise ControlUnavailable("Group owner cannot be removed")
                with self.ui.roster(name):
                    menu = self.ui._context(self.ui._member_row(name))
                    try:
                        leaf = self.ui._menu_item(menu, "移出本群")
                        self.b.invoke(leaf)
                        dialog = wait_until(lambda: self.ui.by_class("q-dialog-main"))
                        content = self.ui.by_class(
                            "q-dialog-content", dialog, required=True
                        )
                        if (
                            text_of(self.b.snapshot(content))
                            != "确定将该成员从本群聊中移出吗？"
                        ):
                            raise ControlUnavailable(
                                "Unrecognized member-removal confirmation"
                            )
                        confirm = self.b.find(
                            dialog,
                            lambda x: (
                                x.ControlTypeName == "ButtonControl"
                                and x.Name == "确定"
                            ),
                        )
                        submitted[0] = True
                        self.b.invoke(confirm)
                        wait_until(
                            lambda: self.ui.by_class("q-dialog-main"),
                            lambda x: x is None,
                        )
                    finally:
                        self.ui._dismiss_menu()
            remaining = self.ui.members(key, query=name)
            if any(m.name == name for m in remaining):
                raise MutationOutcomeUnknown(
                    "Removal submitted; member still appears in the loaded roster"
                )
            return MemberRemovalReceipt(target.id, name, number, True)

    def _custom_ban(self, name, duration, submitted):
        dialog = wait_until(lambda: self.ui.by_class("group-message-forbidden-dialog"))
        try:
            member = self.ui.by_class("name", dialog, required=True)
            if text_of(self.b.snapshot(member)) != name:
                raise TargetMismatch("Custom mute dialog identifies a different member")
            selectors = [
                c
                for c in self.b.walk(dialog)
                if c.ControlTypeName == "ButtonControl"
                and "q-pulldown-menu-button" in c.ClassName.split()
            ]
            labels = [
                c.Name
                for c in self.b.walk(dialog)
                if c.ControlTypeName == "TextControl" and c.Name in {"天", "时", "分钟"}
            ]
            if len(selectors) != 3 or labels != ["天", "时", "分钟"]:
                raise ControlUnavailable("Unrecognized custom mute duration controls")
            days, remainder = divmod(duration, 86400)
            hours, remainder = divmod(remainder, 3600)
            expected = (days, hours, remainder // 60)
            for selector, value in zip(selectors, expected):
                if selector.Name.split() == ["已选中", str(value)]:
                    continue
                selector.GetExpandCollapsePattern().Expand(waitTime=0.1)
                menu = wait_until(lambda: self.ui.by_class("q-pulldown-menu-panel"))
                choices = [
                    c
                    for c in self.b.walk(menu)
                    if c.ControlTypeName == "CheckBoxControl" and c.Name == str(value)
                ]
                if len(choices) != 1 or not choices[0].IsEnabled:
                    raise ControlUnavailable("Requested native mute value unavailable")
                choice = choices[0]
                if choice.IsOffscreen:
                    choice.GetScrollItemPattern().ScrollIntoView(waitTime=0.1)
                choice.GetTogglePattern().Toggle(waitTime=0.1)
                wait_until(
                    lambda selector=selector: selector.Name.split(),
                    lambda actual, value=value: actual == ["已选中", str(value)],
                )
            if [c.Name.split() for c in selectors] != [
                ["已选中", str(v)] for v in expected
            ]:
                raise ControlUnavailable(
                    "Custom mute values changed before confirmation"
                )
            button = self.b.find(
                dialog,
                lambda c: c.ControlTypeName == "ButtonControl" and c.Name == "确定",
            )
            if not button.IsEnabled:
                raise ControlUnavailable("Custom mute confirmation is disabled")
            submitted[0] = True
            self.b.invoke(button)
            wait_until(
                lambda: self.ui.by_class("group-message-forbidden-dialog"),
                lambda current: current is None,
            )
        finally:
            if (
                not submitted[0]
                and self.ui.by_class("group-message-forbidden-dialog") is not None
            ):
                self.ui._dismiss_menu()
                self.b.invoke(
                    self.b.find(
                        dialog,
                        lambda c: (
                            c.Name == "取消" and c.ControlTypeName == "ButtonControl"
                        ),
                    )
                )

    def set_member_ban(self, key, name, duration):
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 100
            or any(ord(c) < 32 for c in name)
        ):
            raise ValueError("Provide an exact loaded member display name")
        if (
            isinstance(duration, bool)
            or not isinstance(duration, int)
            or not 0 <= duration < 30 * 86400
            or duration % 60
        ):
            raise ValueError(
                "duration must be 0 or whole minutes below 30 days, in seconds"
            )
        submitted = [False]
        with ui_transaction(), mutation_guard(submitted):
            with self.ui.checked(key) as target:
                if target.kind != "group":
                    raise ValueError("Member mute requires a group")
                with self.ui.roster(name):
                    row = self.ui._member_row(name)
                    menu = self.ui._context(row)
                    try:
                        before = ban_state(self.ui._actions(menu))
                        if duration == 0:
                            if not before:
                                return MemberBanReceipt(
                                    target.id, name, 0, False, False
                                )
                            leaf = self.ui._menu_item(menu, "解除禁言")
                            if not leaf.IsEnabled:
                                raise ControlUnavailable("Unmute entry is disabled")
                            submitted[0] = True
                            self.b.invoke(leaf)
                        else:
                            if before:
                                raise ControlUnavailable(
                                    "Already muted; current duration is unknown. Unmute explicitly before setting a new duration"
                                )
                            entry = self.ui._menu_item(menu, "设置群内禁言")
                            if (
                                "q-context-sub-menu__item"
                                not in entry.ClassName.split()
                            ):
                                raise ControlUnavailable(
                                    "Expected mute duration cascade is unavailable"
                                )
                            with ExitStack() as stack:
                                pattern = entry.GetPattern(
                                    self.b.auto.PatternId.ExpandCollapsePattern
                                )
                                if pattern:
                                    pattern.Expand(waitTime=0.1)
                                else:
                                    stack.enter_context(hover_control(self.b, entry))
                                submenu = wait_until(
                                    lambda: self.ui.by_class(
                                        "q-context-sub-menu__container"
                                    )
                                )
                                leaf = self.ui._menu_item(
                                    submenu, PRESETS.get(duration, "自定义时长")
                                )
                                if not leaf.IsEnabled:
                                    raise ControlUnavailable(
                                        "Mute duration entry is disabled"
                                    )
                                if duration in PRESETS:
                                    submitted[0] = True
                                    self.b.invoke(leaf)
                                else:
                                    self.b.invoke(leaf)
                                    self._custom_ban(name, duration, submitted)
                    finally:
                        self.ui._dismiss_menu()
            if submitted[0]:
                try:
                    after = ban_state(self.ui.member_actions(key, name))
                except ControlUnavailable as error:
                    raise MutationOutcomeUnknown(
                        "Mute/unmute submitted; UI readback failed, inspect before retrying"
                    ) from error
                if after is not bool(duration):
                    raise MutationOutcomeUnknown(
                        "Mute/unmute submitted; UI state differs, inspect before retrying"
                    )
                return MemberBanReceipt(target.id, name, duration, before, after)
