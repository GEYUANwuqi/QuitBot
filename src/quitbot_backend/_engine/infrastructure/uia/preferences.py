"""Typed, idempotent settings with fresh UI readback; no arbitrary control actions."""

from quitbot_backend._engine.domain.controls import GroupPreferences, SettingReceipt
from quitbot_backend._engine.errors import ControlUnavailable, MutationOutcomeUnknown
from quitbot_backend._engine.locking import ui_transaction
from quitbot_backend._engine.uia import wait_until

from .moderation import mutation_guard

LIMITS = {0: "不限制发言", 5: "每分钟5条", 10: "每分钟10条"}
MESSAGE_MODES = {
    "notify": "允许消息提醒",
    "silent": "接收消息但不提醒",
    "assistant": "收进群助手且不提醒",
    "blocked": "屏蔽群消息",
}


def selected_label(name):
    value = name.strip()
    return value.removeprefix("已选中").strip() if value.startswith("已选中") else None


class PreferencesUI:
    def __init__(self, client):
        self.client = client
        self.ui = client.control_ui
        self.b = client.backend

    def _dropdown(self, panel, label):
        if label == "群消息设置":
            controls = [
                c
                for c in self.b.walk(panel)
                if "q-pulldown-menu-button" in c.ClassName.split()
                and selected_label(c.Name) in MESSAGE_MODES.values()
                and "input-container"
                in c.GetParentControl().GetParentControl().ClassName.split()
            ]
            if len(controls) != 1:
                raise ControlUnavailable("Unique group notification dropdown not found")
            return controls[0]
        containers = [
            c
            for c in self.b.walk(panel)
            if "pulldown-select" in c.ClassName.split()
            and any(
                x.ControlTypeName == "TextControl" and x.Name == label
                for x in self.b.walk(c)
            )
        ]
        if len(containers) != 1:
            raise ControlUnavailable(f"Unique {label} dropdown not found")
        return self.ui.by_class("q-pulldown-menu-button", containers[0], required=True)

    def _select(self, control, label, *, submitted=None, collapse_after_submit=True):
        if not control.IsEnabled:
            raise ControlUnavailable("Setting dropdown is disabled")
        pattern = control.GetPattern(self.b.auto.PatternId.ExpandCollapsePattern)
        if pattern is None:
            raise ControlUnavailable("Setting dropdown has no ExpandCollapse pattern")
        pattern.Expand(waitTime=0.1)
        try:
            choices = [
                c
                for c in self.b.walk(self.b.window)
                if "q-pulldown-menu-list-item" in c.ClassName.split()
                and c.Name == label
                and not c.IsOffscreen
            ]
            if len(choices) != 1 or not choices[0].IsEnabled:
                raise ControlUnavailable("Unique enabled setting choice not found")
            item = choices[0]
            toggle = item.GetPattern(self.b.auto.PatternId.TogglePattern)
            if toggle is None or toggle.ToggleState not in (0, 1):
                raise ControlUnavailable("Native setting selection state unavailable")
            if toggle.ToggleState != 1:
                try:
                    if submitted is not None:
                        submitted[0] = True
                    toggle.Toggle(waitTime=0.15)
                    wait_until(
                        lambda: selected_label(control.Name), lambda x: x == label
                    )
                except Exception as error:
                    raise MutationOutcomeUnknown(
                        "Setting option outcome unknown; inspect before retrying"
                    ) from error
        finally:
            from .inspection import require_unlocked

            if submitted is None or not submitted[0] or collapse_after_submit:
                require_unlocked(self.b.auto)
                pattern.Collapse(waitTime=0.05)

    def group_preferences(self, key):
        with self.ui.checked(key) as target, self.ui.more() as panel:
            if target.kind != "group":
                raise ValueError("Group preferences require a group")
            whole = self.ui._switch_value(self.ui._switch(panel, None, "全员禁言"))
            try:
                label = selected_label(self._dropdown(panel, "发言限频").Name)
                limit = next((k for k, v in LIMITS.items() if v == label), None)
            except ControlUnavailable:
                limit = None
            muted = self.ui._switch_value(self.ui._switch(panel, "set-disturb"))
            mode = "notify" if muted is False else None
            if muted:
                try:
                    label = selected_label(self._dropdown(panel, "群消息设置").Name)
                    mode = next(
                        (k for k, v in MESSAGE_MODES.items() if v == label), None
                    )
                except ControlUnavailable:
                    mode = None
            return GroupPreferences(target.id, whole, limit, mode)

    def _boolean(self, key, enabled, setting, row_class=None, label=None, kind=None):
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be boolean")
        submitted = [False]
        with mutation_guard(submitted), self.ui.checked(key) as target:
            if kind and target.kind != kind:
                raise ValueError(f"{setting} requires a {kind} conversation")
            with self.ui.more() as panel:
                before = self.ui._switch_value(self.ui._switch(panel, row_class, label))
                if before is None:
                    raise ControlUnavailable(f"{setting} switch state unavailable")
                if before != enabled:
                    if not self.ui._switch(panel, row_class, label).IsEnabled:
                        raise ControlUnavailable(f"{setting} switch is disabled")
                    toggle = self.ui._switch(panel, row_class, label).GetTogglePattern()
                    try:
                        submitted[0] = True
                        toggle.Toggle(waitTime=0.15)
                        if self.ui.by_class("q-dialog-main") is not None:
                            raise ControlUnavailable(
                                "Unexpected settings confirmation retained"
                            )
                        wait_until(
                            lambda: self.ui._switch_value(
                                self.ui._switch(panel, row_class, label)
                            ),
                            lambda x: x is enabled,
                        )
                    except Exception as error:
                        raise MutationOutcomeUnknown(
                            f"{setting} outcome unknown; inspect before retrying"
                        ) from error
            with self.ui.more() as panel:
                after = self.ui._switch_value(self.ui._switch(panel, row_class, label))
            if after is not enabled:
                error_type = (
                    MutationOutcomeUnknown if submitted[0] else ControlUnavailable
                )
                raise error_type(f"{setting} was not confirmed on reread")
            return SettingReceipt(target.id, setting, before, after)

    def set_muted(self, key, enabled):
        return self._boolean(key, enabled, "muted", "set-disturb")

    def set_whole_ban(self, key, enabled):
        return self._boolean(
            key, enabled, "whole_banned", label="全员禁言", kind="group"
        )

    def auto_download(self, key):
        with self.ui.checked(key) as target, self.ui.more() as panel:
            if target.kind != "private":
                raise ValueError("Auto download requires a private conversation")
            return self.ui._switch_value(
                self.ui._switch(panel, None, "接收文件后自动下载")
            )

    def set_auto_download(self, key, enabled):
        return self._boolean(
            key, enabled, "auto_download", label="接收文件后自动下载", kind="private"
        )

    def set_speak_limit(self, key, per_minute):
        if type(per_minute) is not int or per_minute not in LIMITS:
            raise ValueError("per_minute must be 0, 5 or 10")
        with ui_transaction():
            return self._set_speak_limit(key, per_minute)

    def _set_speak_limit(self, key, per_minute):
        submitted = [False]
        with mutation_guard(submitted):
            return self._change_speak_limit(key, per_minute, submitted)

    def _change_speak_limit(self, key, per_minute, submitted):
        if type(per_minute) is not int or per_minute not in LIMITS:
            raise ValueError("per_minute must be 0, 5 or 10")
        before = self.group_preferences(key).speak_limit
        if before is None:
            raise ControlUnavailable("Current speak limit unavailable")
        if before != per_minute:
            with self.ui.checked(key), self.ui.more() as panel:
                self._select(
                    self._dropdown(panel, "发言限频"),
                    LIMITS[per_minute],
                    submitted=submitted,
                )
        after = self.group_preferences(key).speak_limit
        if after != per_minute:
            error_type = MutationOutcomeUnknown if submitted[0] else ControlUnavailable
            raise error_type("Speak limit was not confirmed on reread")
        return SettingReceipt(
            str(key.id if hasattr(key, "id") else key),
            "speak_limit",
            str(before),
            str(after),
        )

    def set_message_mode(self, key, mode):
        if mode not in MESSAGE_MODES:
            raise ValueError("mode must be notify/silent/assistant/blocked")
        with ui_transaction():
            return self._set_message_mode(key, mode)

    def _set_message_mode(self, key, mode):
        target = self.client.target(key)
        before = self.group_preferences(key).message_mode
        if before is None:
            raise ControlUnavailable("Current group message mode unavailable")
        if before == mode:
            return SettingReceipt(target.id, "message_mode", before, before)
        submitted = [False]
        gate_changed = False
        try:
            receipt = self.set_muted(key, mode != "notify")
            gate_changed = receipt.before != receipt.after
            if mode != "notify":
                with self.ui.checked(key), self.ui.more() as panel:
                    self._select(
                        self._dropdown(panel, "群消息设置"),
                        MESSAGE_MODES[mode],
                        submitted=submitted,
                        collapse_after_submit=False,
                    )
        except Exception as error:
            if (
                not submitted[0]
                and not gate_changed
                and not isinstance(error, MutationOutcomeUnknown)
            ):
                raise
            # Re-read first: a detached group window often remains valid even
            # when the main conversation moves into the assistant. Never replay.
        try:
            after = self.group_preferences(key).message_mode
        except Exception:  # noqa: BLE001 - UI invalidation after mode change; read-only recovery
            try:
                self.client.open_chat(target, route="contacts")
                after = self.group_preferences(key).message_mode
            except Exception as error:
                raise MutationOutcomeUnknown(
                    "Group mode submitted but current verified state unavailable"
                ) from error
        if after != mode:
            raise MutationOutcomeUnknown("Message mode was not confirmed on reread")
        return SettingReceipt(
            str(key.id if hasattr(key, "id") else key), "message_mode", before, after
        )
