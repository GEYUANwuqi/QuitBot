"""Group administrator changes through observed, identity-checked dialogs."""

from quitbot_backend._engine.domain.administration import MemberAdminReceipt
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    MutationOutcomeUnknown,
    TargetMismatch,
)
from quitbot_backend._engine.locking import ui_transaction
from quitbot_backend._engine.models import text_of
from quitbot_backend._engine.ui.backend import wait_until

from .inspection import require_unlocked
from .moderation import mutation_guard

ADMIN_LABELS = {"设为管理员": False, "取消管理员": True}


def admin_state(actions):
    matches = [action for action in actions if action.name in ADMIN_LABELS]
    if len(matches) != 1 or not matches[0].enabled:
        raise ControlUnavailable("Administrator state/permission is not unique")
    return ADMIN_LABELS[matches[0].name]


class AdministrationUI:
    def __init__(self, client):
        self.ui, self.b = client.control_ui, client.backend

    def _confirmation(self, dialog, name, enabled):
        title = self.ui.by_class("q-dialog-title", dialog, required=True)
        content = self.ui.by_class("q-dialog-content", dialog, required=True)
        expected_title = "设为管理员" if enabled else "取消管理员"
        expected_content = (
            f"确定要设置 {name} 为管理员吗？"
            if enabled
            else f"确定要取消 {name} 管理员权限吗？"
        )
        if (
            text_of(self.b.snapshot(title)) != expected_title
            or text_of(self.b.snapshot(content)) != expected_content
        ):
            raise ControlUnavailable("Unrecognized administrator confirmation")
        controls = list(self.b.walk(dialog))
        if any(
            c.ControlTypeName
            in {
                "EditControl",
                "CheckBoxControl",
                "RadioButtonControl",
                "ComboBoxControl",
            }
            for c in controls
        ):
            raise ControlUnavailable("Unexpected administrator confirmation options")
        buttons = [c for c in controls if c.ControlTypeName == "ButtonControl"]
        if sorted(c.Name for c in buttons) != sorted(("关闭", "确定", "取消")):
            raise ControlUnavailable("Administrator confirmation buttons changed")
        confirm = next(c for c in buttons if c.Name == "确定")
        if not confirm.IsEnabled or confirm.IsOffscreen:
            raise ControlUnavailable("Administrator confirmation is not available")
        return confirm

    def set_member_admin(self, key, name, enabled, *, expected_user_id):
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 100
            or any(ord(c) < 32 for c in name)
        ):
            raise ValueError("Provide an exact member display name")
        if type(enabled) is not bool:
            raise TypeError("enabled must be boolean")
        if (
            isinstance(expected_user_id, bool)
            or not str(expected_user_id).isascii()
            or not str(expected_user_id).isdigit()
            or not 5 <= len(str(expected_user_id)) <= 12
        ):
            raise ValueError("An expected QQ number is required")
        number = str(expected_user_id)
        submitted = [False]
        with (
            ui_transaction(),
            mutation_guard(submitted),
            self.ui.checked(key) as target,
        ):
            if target.kind != "group":
                raise ValueError("Administrator changes require a group")
            if self.ui.by_class("q-dialog-main") is not None:
                raise ControlUnavailable(
                    "Existing dialog retained before administrator change"
                )
            profile = self.ui.member_profile(key, name)
            if profile.user_id != number:
                raise TargetMismatch("Member QQ number differs from expected_user_id")
            if profile.role not in {"member", "admin"}:
                raise ControlUnavailable(
                    "Group owner or unknown role cannot be changed"
                )
            with self.ui.roster(name):
                menu = self.ui._context(self.ui._member_row(name))
                try:
                    before = admin_state(self.ui._actions(menu))
                    if before != (profile.role == "admin"):
                        raise TargetMismatch(
                            "Member role and administrator menu disagree"
                        )
                    if before is enabled:
                        return MemberAdminReceipt(
                            target.id, name, number, before, before
                        )
                    label = "设为管理员" if enabled else "取消管理员"
                    leaf = self.ui._menu_item(menu, label)
                    if not leaf.IsEnabled:
                        raise ControlUnavailable("Administrator action is disabled")
                    # This observed menu action opens a confirmation; no role
                    # mutation is submitted until its exact content is checked.
                    try:
                        self.b.invoke(leaf)
                        dialog = wait_until(lambda: self.ui.by_class("q-dialog-main"))
                    except Exception as error:
                        raise MutationOutcomeUnknown(
                            "Administrator action outcome unavailable; inspect QQ before retrying"
                        ) from error
                    confirm = self._confirmation(dialog, name, enabled)
                    require_unlocked(self.b.auto)
                    if self.b.header().Name != target.name:
                        raise TargetMismatch(
                            "Conversation changed before administrator confirmation"
                        )
                    submitted[0] = True
                    self.b.invoke(confirm)
                    wait_until(
                        lambda: self.ui.by_class("q-dialog-main"), lambda d: d is None
                    )
                finally:
                    # Retain unfamiliar dialogs; never dismiss them with Escape.
                    if self.ui.by_class("q-dialog-main") is None:
                        self.ui._dismiss_menu()

            def role():
                members = [
                    m for m in self.ui.members(key, query=name) if m.name == name
                ]
                if len(members) != 1 or members[0].role not in {"member", "admin"}:
                    raise TargetMismatch("Member role readback is missing or ambiguous")
                return members[0].role == "admin"

            wait_until(role, lambda current: current is enabled)
            after = admin_state(self.ui.member_actions(key, name))
            if after is not enabled:
                raise MutationOutcomeUnknown(
                    "Administrator menu does not confirm the requested role"
                )
            return MemberAdminReceipt(target.id, name, number, before, after)
