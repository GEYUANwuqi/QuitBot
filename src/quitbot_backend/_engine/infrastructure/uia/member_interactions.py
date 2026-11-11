"""Identity guards shared by native group member interactions."""

from contextlib import contextmanager

from quitbot_backend._engine.domain.activities import system_text
from quitbot_backend._engine.domain.member_interactions import (
    MemberCardReceipt,
    MemberPokeReceipt,
)
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    MutationOutcomeUnknown,
    TargetMismatch,
)
from quitbot_backend._engine.locking import ui_transaction
from quitbot_backend._engine.models import text_of
from quitbot_backend._engine.uia import wait_until

from .inspection import require_unlocked
from .moderation import mutation_guard


def member_identity(name, expected_user_id):
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 100
        or any(ord(character) < 32 for character in name)
    ):
        raise ValueError("Provide an exact member display name")
    number = str(expected_user_id)
    if (
        isinstance(expected_user_id, bool)
        or not number.isascii()
        or not number.isdigit()
        or not 5 <= len(number) <= 12
    ):
        raise ValueError("An expected QQ number is required")
    return number


class MemberInteractionsUI:
    def __init__(self, client):
        self.client = client
        self.ui, self.b = client.control_ui, client.backend

    def poke_friend(self, key):
        """Poke the verified peer via a loaded incoming message's avatar."""
        from comtypes.gen.UIAutomationClient import IUIAutomationElement3

        ui, b = self.ui, self.b
        submitted = [False]
        with ui_transaction(), mutation_guard(submitted), ui.checked(key) as target:
            if target.kind != "private":
                raise ValueError("Friend poke requires a private conversation")
            if ui.by_class("q-dialog-main") is not None:
                raise ControlUnavailable("Existing dialog retained before friend poke")
            loaded = self.client._read(target)
            incoming = [m for m in loaded if m.direction == "incoming"]
            if not incoming:
                raise ControlUnavailable(
                    "Friend poke needs a loaded incoming message avatar"
                )
            before = {m.id for m in loaded}
            rows = {
                c.AutomationId: c for c in b.walk(b.window)
                if "ml-item" in c.ClassName.split()
            }
            avatar = None
            for message in reversed(incoming):
                row = rows.get(message.id)
                candidate = ui.by_class("avatar-span", row) if row is not None else None
                # Some automated notices expose a nameless avatar. Keep the
                # identity guard and use another already loaded peer message.
                if candidate is not None and candidate.Name == target.name and candidate.IsEnabled:
                    avatar = candidate
                    source_id = message.id
                    break
            if avatar is None:
                raise ControlUnavailable("No loaded incoming avatar identifies the verified peer")
            if avatar.IsOffscreen:
                scroll = avatar.GetPattern(b.auto.PatternId.ScrollItemPattern)
                if scroll is None:
                    raise ControlUnavailable("Verified incoming avatar cannot be revealed")
                scroll.ScrollIntoView(waitTime=0.2)
            row = b.find(b.window, lambda c: c.AutomationId == source_id
                         and "ml-item" in c.ClassName.split())
            avatar = ui.by_class("avatar-span", row, required=True)
            if avatar.Name != target.name or avatar.IsOffscreen or not avatar.IsEnabled:
                raise TargetMismatch("Incoming avatar identity/visibility changed before poke")
            avatar.Element.QueryInterface(IUIAutomationElement3).ShowContextMenu()
            try:
                menu = wait_until(lambda: ui.by_class("buddy-avatar-context-menu"))
                button = ui._menu_item(menu, "戳一戳")
                if not button.IsEnabled or button.IsOffscreen:
                    raise ControlUnavailable("Friend poke menu is unavailable")
                require_unlocked(b.auto)
                if b.header().Name != target.name:
                    raise TargetMismatch("Conversation changed before friend poke")
                submitted[0] = True
                b.invoke(button)
            finally:
                ui._dismiss_menu()
            def result():
                matches = [
                    m
                    for m in self.client._read(target)
                    if m.id not in before
                    and m.kind == "system"
                    and m.direction == "system"
                    and system_text(m) == f"你戳了戳{target.name}"
                ]
                if len(matches) > 1:
                    raise ControlUnavailable(
                        "Multiple new friend poke rows are ambiguous"
                    )
                return matches[0] if matches else None

            return wait_until(result)

    @contextmanager
    def _checked_member(self, key, name, expected_user_id):
        number = member_identity(name, expected_user_id)
        with self.ui.checked(key) as target:
            if target.kind != "group":
                raise ValueError("Member interactions require a group")
            if self.ui.by_class("q-dialog-main") is not None:
                raise ControlUnavailable(
                    "Existing dialog retained before member action"
                )
            profile = self.ui.member_profile(key, name)
            if profile.user_id != number:
                raise TargetMismatch("Member QQ number differs from expected_user_id")
            yield target, profile

    def _card_dialog(self, dialog):
        if "group-edit-dialog" not in dialog.ClassName.split():
            raise ControlUnavailable("Unrecognized member card dialog")
        title = self.ui.by_class("q-dialog-title", dialog, required=True)
        if text_of(self.b.snapshot(title)) != "修改群昵称":
            raise ControlUnavailable("Unrecognized member card dialog title")
        controls = list(self.b.walk(dialog))
        if any(
            control.ControlTypeName
            in {"CheckBoxControl", "RadioButtonControl", "ComboBoxControl"}
            for control in controls
        ):
            raise ControlUnavailable("Unexpected member card dialog options")
        edits = [
            control for control in controls if control.ControlTypeName == "EditControl"
        ]
        buttons = [
            control
            for control in controls
            if control.ControlTypeName == "ButtonControl"
        ]
        if (
            len(edits) != 1
            or "el-textarea__inner" not in edits[0].ClassName.split()
            or sorted(control.Name for control in buttons) != ["关闭", "取消", "确定"]
        ):
            raise ControlUnavailable("Member card editor shape changed")
        if (
            not edits[0].IsEnabled
            or edits[0].IsOffscreen
            or edits[0].GetValuePattern() is None
        ):
            raise ControlUnavailable("Member card editor is unavailable")
        return edits[0], {control.Name: control for control in buttons}

    @contextmanager
    def _card_editor(self, name):
        with self.ui.roster(name):
            menu = self.ui._context(self.ui._member_row(name))
            try:
                leaf = self.ui._menu_item(menu, "修改群昵称")
                if not leaf.IsEnabled or leaf.IsOffscreen:
                    raise ControlUnavailable("Member card action is disabled")
                self.b.invoke(leaf)
                dialog = wait_until(lambda: self.ui.by_class("q-dialog-main"))
                field, buttons = self._card_dialog(dialog)
                yield dialog, field, buttons
            finally:
                # Unrecognized or interrupted dialogs remain visible for inspection.
                if self.ui.by_class("q-dialog-main") is None:
                    self.ui._dismiss_menu()

    def _cancel_card(self, dialog):
        _, buttons = self._card_dialog(dialog)
        cancel = buttons["取消"]
        if not cancel.IsEnabled or cancel.IsOffscreen:
            raise ControlUnavailable("Member card cancel is unavailable")
        self.b.invoke(cancel)
        wait_until(
            lambda: self.ui.by_class("q-dialog-main"), lambda current: current is None
        )

    def _forget_identity(self, group_id, number):
        for key, bound in tuple(self.ui.identities.items()):
            if key[0] == group_id and bound == number:
                del self.ui.identities[key]

    def poke_member(self, key, name, *, expected_user_id):
        number = member_identity(name, expected_user_id)
        submitted = [False]
        with (
            ui_transaction(),
            mutation_guard(submitted),
            self._checked_member(key, name, number) as (target, _profile),
        ):
            before = {m.id for m in self.client._read(target)}
            with self.ui.roster(name):
                menu = self.ui._context(self.ui._member_row(name))
                try:
                    leaf = self.ui._menu_item(menu, "戳一戳")
                    if not leaf.IsEnabled or leaf.IsOffscreen:
                        raise ControlUnavailable("Poke action is unavailable")
                    require_unlocked(self.b.auto)
                    if self.b.header().Name != target.name:
                        raise TargetMismatch("Conversation changed before poke")
                    submitted[0] = True
                    self.b.invoke(leaf)
                finally:
                    self.ui._dismiss_menu()

            def result():
                matches = [
                    m
                    for m in self.client._read(target)
                    if m.id not in before
                    and m.kind == "system"
                    and m.direction == "system"
                    and system_text(m) == f"你戳了戳{name}"
                ]
                if len(matches) > 1:
                    raise ControlUnavailable("Multiple new poke rows are ambiguous")
                return matches[0] if matches else None

            row = wait_until(result)
            return MemberPokeReceipt(target.id, number, name, row.id, system_text(row))

    def set_member_card(self, key, name, value, *, expected_user_id):
        number = member_identity(name, expected_user_id)
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 60
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("Member card must be 1..60 characters without controls")
        submitted = [False]
        with (
            ui_transaction(),
            mutation_guard(submitted),
            self._checked_member(key, name, number) as (target, profile),
        ):
            if name != value and any(
                member.name == value for member in self.ui.members(key, query=value)
            ):
                raise TargetMismatch("Requested card matches another loaded member")
            try:
                with self._card_editor(name) as (dialog, field, buttons):
                    before = field.GetValuePattern().Value
                    if before == value:
                        self._cancel_card(dialog)
                        return MemberCardReceipt(
                            target.id,
                            number,
                            before,
                            before,
                            name,
                            name,
                            profile.nickname,
                        )
                    self.b.focus_control(field)
                    field.GetValuePattern().SetValue(value, waitTime=0.1)
                    wait_until(
                        lambda: self._card_dialog(dialog)[0].GetValuePattern().Value,
                        lambda current: current == value,
                    )
                    _, buttons = self._card_dialog(dialog)
                    confirm = buttons["确定"]
                    if not confirm.IsEnabled or confirm.IsOffscreen:
                        raise ControlUnavailable("Member card confirm is unavailable")
                    require_unlocked(self.b.auto)
                    if self.b.header().Name != target.name:
                        raise TargetMismatch(
                            "Conversation changed before member card confirmation"
                        )
                    submitted[0] = True
                    self.b.invoke(confirm)
                    wait_until(
                        lambda: self.ui.by_class("q-dialog-main"),
                        lambda current: current is None,
                    )
            finally:
                if submitted[0]:
                    self._forget_identity(target.id, number)

            def renamed_member():
                matches = [
                    member
                    for member in self.ui.members(key, query=value)
                    if member.name == value
                ]
                return matches[0] if len(matches) == 1 else None

            wait_until(renamed_member)
            after_profile = self.ui.member_profile(key, value)
            if after_profile.user_id != number:
                self._forget_identity(target.id, number)
                raise MutationOutcomeUnknown(
                    "Renamed member QQ identity was not verified"
                )
            with self._card_editor(value) as (dialog, field, _):
                after = field.GetValuePattern().Value
                self._cancel_card(dialog)
                if after != value:
                    raise MutationOutcomeUnknown(
                        "Member card persisted value differs from requested value"
                    )
            return MemberCardReceipt(
                target.id, number, before, after, name, value, profile.nickname
            )
