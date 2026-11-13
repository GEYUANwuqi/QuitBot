"""Group administration through observed settings controls, with readback."""

from contextlib import contextmanager

from ...domain.controls import SettingReceipt
from ...domain.group_settings import (
    GroupJoinPolicy,
    GroupJoinPolicyReceipt,
    GroupTagReceipt,
)
from ...errors import ControlUnavailable, MutationOutcomeUnknown
from ...locking import ui_transaction
from ...models import text_of
from ...ui.backend import wait_until
from .moderation import mutation_guard

JOIN_MODES = {
    "open": "允许任何人加群",
    "verify": "需要身份验证",
    "closed": "不允许任何人加群",
}
APPROVALS = {
    "direct": "无需审核直接进群",
    "admin": "需要管理员审核",
    "under100": "100人内无需审核",
}
SWITCHES = {
    "member_invites": "允许群成员邀请好友加群",
    "bot_invites": "允许群成员邀请机器人加群",
}
SECTIONS = {"invite_approval": "邀请加群权限", "bot_invite_approval": "机器人加群权限"}


class GroupSettingsUI:
    def __init__(self, chat):
        self.ui, self.b = chat, chat.b

    def share_link(self, key):
        """Copy the client's group link using the desktop session's clipboard."""
        import re

        from ...platforms import clipboard_sequence

        ui, b = self.ui, self.b
        with ui.checked(key) as target:
            if target.kind != "group":
                raise ValueError("Group sharing requires a group")
            if ui.by_class("q-dialog-main") is not None:
                raise ControlUnavailable("Existing dialog retained before sharing")
            with ui.more() as panel:
                b.invoke(ui.by_class("group-profile-card__share", panel, required=True))
                dialog = wait_until(lambda: ui.by_class("qrcode-viewer-dialog"))
                try:
                    fields = [
                        c.Name
                        for c in b.walk(dialog)
                        if c.ControlTypeName == "TextControl"
                    ]
                    if target.id not in fields or "群号：" not in fields:
                        raise ControlUnavailable(
                            "Group QR identity differs from the target"
                        )
                    button = b.find(
                        dialog,
                        lambda c: (
                            "btn-item" in c.ClassName.split()
                            and text_of(b.snapshot(c)) == "复制链接"
                        ),
                    )
                    sequence = clipboard_sequence(b.auto)
                    b.invoke(button)
                    wait_until(
                        lambda: clipboard_sequence(b.auto),
                        lambda value: value != sequence,
                    )
                    copied = b.auto.GetClipboardText()
                    match = re.fullmatch(
                        re.escape(f"点击链接加入群聊【{target.name}】：")
                        + r"(https://qm\.qq\.com/q/[A-Za-z0-9_-]+)",
                        copied.strip(),
                    )
                    if match is None:
                        raise ControlUnavailable(
                            "Copied text is not the verified group's share link"
                        )
                    return match[1]
                finally:
                    if ui.by_class("qrcode-viewer-dialog") is not None:
                        b.invoke(
                            b.find(
                                dialog,
                                lambda c: (
                                    c.Name == "关闭"
                                    and c.ControlTypeName == "ButtonControl"
                                ),
                            )
                        )
                        wait_until(
                            lambda: ui.by_class("qrcode-viewer-dialog"),
                            lambda c: c is None,
                        )

    @contextmanager
    def _tags_dialog(self, key):
        with self.page(key, "群资料设置") as (target, panel):
            if self.ui.by_class("q-dialog-main") is not None:
                raise ControlUnavailable("Existing group dialog retained")
            self.b.invoke(self.ui.by_class("group-label", panel, required=True))
            dialog = wait_until(lambda: self.ui.by_class("group-label-dialog"))
            try:
                yield target, dialog
            finally:
                current = self.ui.by_class("group-label-dialog")
                if current is not None:
                    self.b.invoke(
                        self.b.find(
                            current,
                            lambda c: (
                                c.ControlTypeName == "ButtonControl"
                                and c.Name == "取消"
                            ),
                        )
                    )
                    self._discard_tag_draft()

    def _discard_tag_draft(self):
        for dialog in list(self.b.walk(self.b.window)):
            if "q-dialog-main" not in dialog.ClassName.split():
                continue
            content = self.ui.by_class("q-dialog-content", dialog)
            if content is not None and text_of(self.b.snapshot(content)) == (
                "有未保存的修改，确认放弃保存？"
            ):
                self.b.invoke(
                    self.b.find(
                        dialog,
                        lambda c: (
                            c.ControlTypeName == "ButtonControl" and c.Name == "确定"
                        ),
                    )
                )
                break

    def _tag_label(self, row):
        return row.Name or text_of(self.b.snapshot(row))

    def _tags(self, dialog):
        rows = [
            c
            for c in self.b.walk(dialog)
            if c.ControlTypeName == "ListItemControl"
            and "custom-tag-wrapper" not in c.ClassName.split()
        ]
        labels = tuple(self._tag_label(c) for c in rows)
        if any(not s for s in labels) or len(set(labels)) != len(labels):
            raise ControlUnavailable("Group tag rows are empty or ambiguous")
        # This title distinguishes a loaded empty list from an unfinished page.
        if not any(c.Name == f"群标签（{len(labels)}/5）" for c in self.b.walk(dialog)):
            return None
        return labels

    def tags(self, key):
        with self._tags_dialog(key) as (_, dialog):
            return wait_until(lambda: self._tags(dialog), lambda v: v is not None)

    def set_tag(self, key, label, enabled):
        if (
            not isinstance(label, str)
            or not label.strip()
            or label != label.strip()
            or len(label) > 20
            or any(ord(c) < 32 for c in label)
        ):
            raise ValueError("Tag must be a nonblank label of at most 20 characters")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        submitted = [False]
        with ui_transaction(), mutation_guard(submitted):
            with self._tags_dialog(key) as (target, dialog):
                before = wait_until(lambda: self._tags(dialog), lambda v: v is not None)
                if (label in before) == enabled:
                    return GroupTagReceipt(target.id, label, before, before)
                if enabled:
                    if len(before) >= 5:
                        raise ControlUnavailable("QQ permits at most five group tags")
                    self.b.invoke(self.ui.by_class("custom-tag", dialog, required=True))
                    edit = wait_until(
                        lambda: self.b.find(
                            dialog, lambda c: c.ControlTypeName == "EditControl", False
                        )
                    )
                    if edit.GetValuePattern().Value:
                        raise ControlUnavailable("Existing custom tag draft retained")
                    self.b.focus_control(edit)
                    self.b.type_literal(edit, label)
                    wait_until(
                        lambda: edit.GetValuePattern().Value, lambda v: v == label
                    )
                    self.b.ensure_focus(edit)
                    self.b.auto.SendKeys("{Enter}", waitTime=0.1)
                    expected = before + (label,)
                else:
                    row = self.b.find(
                        dialog,
                        lambda c: (
                            c.ControlTypeName == "ListItemControl"
                            and self._tag_label(c) == label
                        ),
                    )
                    self.b.invoke(self.ui.by_class("q-svg-icon", row, required=True))
                    expected = tuple(s for s in before if s != label)
                wait_until(lambda: self._tags(dialog), lambda v: v == expected)
                button = self.b.find(
                    dialog,
                    lambda c: c.ControlTypeName == "ButtonControl" and c.Name == "保存",
                )
                submitted[0] = True
                self.b.invoke(button)
                wait_until(
                    lambda: self.ui.by_class("group-label-dialog"), lambda v: v is None
                )
            after = self.tags(key)
            if after != expected:
                raise MutationOutcomeUnknown(
                    "Saved group tags differ; inspect before retrying"
                )
            return GroupTagReceipt(target.id, label, before, after)

    def description(self, key):
        with (
            self.page(key, "群资料设置") as (_, panel),
            self._description_editor(panel) as (_, editor),
        ):
            return self._description_value(editor)

    @staticmethod
    def _description_value(editor):
        value = editor.GetValuePattern().Value.rstrip("\n")
        return "" if value == "编辑群介绍，1-900字符" else value

    @contextmanager
    def _description_editor(self, panel):
        if self.ui.by_class("q-dialog-main") is not None:
            raise ControlUnavailable("Existing dialog retained")
        self.b.invoke(self.ui.by_class("group-introduction", panel, required=True))
        dialog = wait_until(lambda: self.ui.by_class("group-introduction-dialog"))
        try:
            editor = wait_until(
                lambda: self.b.find(
                    dialog,
                    lambda c: (
                        c.ControlTypeName == "EditControl"
                        and c.Name == "编辑器"
                        and "ck-content" in c.ClassName.split()
                    ),
                    False,
                )
            )
            yield dialog, editor
        finally:
            if self.ui.by_class("group-introduction-dialog") is not None:
                self.b.invoke(
                    self.b.find(
                        dialog,
                        lambda c: (
                            c.ControlTypeName == "ButtonControl" and c.Name == "取消"
                        ),
                    )
                )
                wait_until(
                    lambda: self.ui.by_class("group-introduction-dialog"),
                    lambda current: current is None,
                )

    def set_description(self, key, value):
        if (
            not isinstance(value, str)
            or len(value) > 900
            or any(ord(c) < 32 and c != "\n" for c in value)
        ):
            raise ValueError("Group introduction must be at most 900 characters")
        submitted = [False]
        with ui_transaction(), mutation_guard(submitted):
            return self._set_description(key, value, submitted)

    def _set_description(self, key, value, submitted):
        with (
            self.page(key, "群资料设置") as (target, panel),
            self._description_editor(panel) as (dialog, editor),
        ):
            # The profile summary collapses line breaks; the editor retains
            # the persisted value and is closed without saving on reads.
            before = self._description_value(editor)
            if before == value:
                return SettingReceipt(target.id, "description", before, value)
            self.b.focus_control(editor)
            self.b.auto.SendKeys("{Ctrl}a", waitTime=0.05)
            if value:
                self.b.type_literal(editor, value)
            else:
                self.b.ensure_focus(editor)
                self.b.auto.SendKeys("{Delete}", waitTime=0.05)
            wait_until(
                lambda: self._description_value(editor),
                lambda actual: actual == value,
            )
            button = self.b.find(
                dialog,
                lambda c: c.ControlTypeName == "ButtonControl" and c.Name == "确定",
            )
            if not button.IsEnabled:
                raise ControlUnavailable("Introduction confirmation disabled")
            submitted[0] = True
            self.b.invoke(button)
            wait_until(
                lambda: self.ui.by_class("group-introduction-dialog"),
                lambda actual: actual is None,
            )
        if self.description(key) != value:
            raise MutationOutcomeUnknown(
                "Introduction did not persist; inspect before retrying"
            )
        return SettingReceipt(target.id, "description", before, value)

    @contextmanager
    def page(self, key, title):
        with self.ui.checked(key) as target:
            if target.kind != "group":
                raise ValueError("Group settings require a group")
            with self.ui.more() as panel:
                entry = self.b.find(
                    panel,
                    lambda c: c.ControlTypeName == "ButtonControl" and c.Name == title,
                )
                if not entry.IsEnabled:
                    raise ControlUnavailable(f"Group setting disabled: {title}")
                self.b.invoke(entry)

                def current():
                    panel = self.ui.by_class("side-panel")
                    if panel is None:
                        return None
                    back = self.b.find(
                        panel,
                        lambda c: (
                            "title" in c.ClassName.split()
                            and text_of(self.b.snapshot(c)) == title
                        ),
                        False,
                    )
                    return panel if back else None

                panel = wait_until(current)
                try:
                    yield target, panel
                finally:
                    panel = current()
                    if panel is not None and self.ui.by_class("q-dialog-main") is None:
                        self.b.invoke(
                            self.b.find(
                                panel,
                                lambda c: (
                                    "title" in c.ClassName.split()
                                    and text_of(self.b.snapshot(c)) == title
                                ),
                            )
                        )
                        wait_until(self.ui._settings_panel)

    def _row(self, panel, label):
        return self.b.find(
            panel,
            lambda c: (
                "q-list-item" in c.ClassName.split()
                and any(x.Name == label for x in self.b.walk(c))
            ),
        )

    def _radio(self, panel, label):
        return self.ui.by_class("q-radio", self._row(panel, label), required=True)

    def _dropdown(self, panel, section):
        wrapper = self.ui.by_class("wrapper", panel, required=True)
        active, candidates = False, []
        for child in wrapper.GetChildren():
            if "field-title" in child.ClassName.split():
                active = text_of(self.b.snapshot(child)) == section
            elif active:
                candidates.extend(
                    c
                    for c in self.b.walk(child)
                    if "q-pulldown-menu-button" in c.ClassName.split()
                )
        if len(candidates) != 1:
            raise ControlUnavailable(
                f"Expected one approval control in {section}, got {len(candidates)}"
            )
        return candidates[0]

    def _read(self, panel, field):
        if field == "join_mode":
            selected = [
                mode
                for mode, label in JOIN_MODES.items()
                if "is-checked" in self._radio(panel, label).ClassName.split()
            ]
            if len(selected) != 1:
                raise ControlUnavailable("Group admission selection is ambiguous")
            return selected[0]
        if field in SWITCHES:
            value = self.ui._switch_value(
                self.ui.by_class(
                    "q-switch", self._row(panel, SWITCHES[field]), required=True
                )
            )
            if value is None:
                raise ControlUnavailable(f"Unknown switch state: {field}")
            return value
        control = self._dropdown(panel, SECTIONS[field])
        values = [
            key
            for key, label in APPROVALS.items()
            if control.Name.strip() == f"已选中 {label}"
        ]
        if len(values) != 1:
            raise ControlUnavailable(f"Unknown approval state: {control.Name!r}")
        return values[0]

    def read(self, key):
        with self.page(key, "加群方式") as (target, _panel):
            return wait_until(
                lambda: self._policy(
                    target, self.ui.by_class("side-panel", required=True)
                )
            )

    def _policy(self, target, panel):
        # The title can arrive before the radio rows finish rendering.
        wait_until(
            lambda: self.b.find(
                panel, lambda c: "q-radio" in c.ClassName.split(), False
            )
        )
        verification = None
        for label in (
            "需要发送验证消息",
            "需要回答问题并由管理员审核",
            "需要正确回答问题",
        ):
            row = self.b.find(
                panel,
                lambda c, label=label: (
                    "q-list-item" in c.ClassName.split()
                    and any(x.Name == label for x in self.b.walk(c))
                ),
                False,
            )
            radio = self.ui.by_class("q-radio", row) if row is not None else None
            if radio is not None and "is-checked" in radio.ClassName.split():
                verification = label
        mode = self._read(panel, "join_mode")

        def invitation(field):
            if (
                mode == "closed"
                and self.b.find(panel, lambda c: c.Name == SWITCHES[field], False)
                is None
            ):
                return None
            return self._read(panel, field)

        members = invitation("member_invites")
        bots = invitation("bot_invites")
        return GroupJoinPolicy(
            target.id,
            mode,
            verification,
            members,
            self._read(panel, "invite_approval") if members else None,
            bots,
            self._read(panel, "bot_invite_approval") if bots else None,
        )

    def set(self, key, field, value):
        if field == "join_mode":
            if not isinstance(value, str) or value not in JOIN_MODES:
                raise ValueError("join_mode must be open/verify/closed")
        elif field in SWITCHES:
            if type(value) is not bool:
                raise ValueError(f"{field} must be boolean")
        elif field in SECTIONS:
            if (
                not isinstance(value, str)
                or value not in APPROVALS
                or (field == "bot_invite_approval" and value == "under100")
            ):
                raise ValueError("Invalid invite approval mode")
        else:
            raise ValueError("Unknown group join setting")
        submitted = [False]
        with ui_transaction(), mutation_guard(submitted):
            return self._set(key, field, value, submitted)

    def _set(self, key, field, value, submitted):
        with self.page(key, "加群方式") as (target, panel):
            before_policy = wait_until(
                lambda: self._policy(
                    target, self.ui.by_class("side-panel", required=True)
                )
            )
            before = self._read(panel, field)
            if before != value:
                if field == "join_mode":
                    row = self._row(panel, JOIN_MODES[value])
                    if not row.IsEnabled:
                        raise ControlUnavailable("Admission option disabled")
                    submitted[0] = True
                    self.b.invoke(row)
                elif field in SWITCHES:
                    switch = self.ui.by_class(
                        "q-switch", self._row(panel, SWITCHES[field]), required=True
                    )
                    if not switch.IsEnabled:
                        raise ControlUnavailable(f"Disabled setting: {field}")
                    submitted[0] = True
                    switch.GetTogglePattern().Toggle(waitTime=0.2)
                else:
                    control = self._dropdown(panel, SECTIONS[field])
                    if not control.IsEnabled:
                        raise ControlUnavailable(f"Disabled setting: {field}")
                    control.GetExpandCollapsePattern().Expand(waitTime=0.1)
                    try:
                        choice = wait_until(
                            lambda: self.b.find(
                                self.b.window,
                                lambda c: (
                                    "q-pulldown-menu-list-item" in c.ClassName.split()
                                    and c.Name == APPROVALS[value]
                                ),
                                False,
                            )
                        )
                        if not choice.IsEnabled:
                            raise ControlUnavailable(f"Disabled choice: {value}")
                        submitted[0] = True
                        self.b.invoke(choice)
                    finally:
                        current = self._dropdown(panel, SECTIONS[field])
                        if current.GetExpandCollapsePattern().ExpandCollapseState == 1:
                            current.GetExpandCollapsePattern().Collapse(waitTime=0.1)
                try:
                    wait_until(
                        lambda: self._read(panel, field), lambda actual: actual == value
                    )
                except Exception as error:
                    raise MutationOutcomeUnknown(
                        f"{field} submission uncertain; read state before retrying"
                    ) from error
        # Reopen: a locally toggled control alone does not prove persistence.
        with self.page(key, "加群方式") as (_, panel):
            after_policy = wait_until(
                lambda: self._policy(
                    target, self.ui.by_class("side-panel", required=True)
                )
            )
            if getattr(after_policy, field) != value:
                raise MutationOutcomeUnknown(
                    f"{field} did not persist; inspect state before retrying"
                )
        return GroupJoinPolicyReceipt(target.id, field, before_policy, after_policy)
