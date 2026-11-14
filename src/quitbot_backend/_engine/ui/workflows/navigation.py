"""Account- and conversation-scoped navigation and native settings reads."""

from contextlib import contextmanager

from quitbot_backend._engine.domain.controls import SettingReceipt
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    MutationOutcomeUnknown,
    TargetMismatch,
)
from quitbot_backend._engine.locking import ui_transaction
from quitbot_backend._engine.ui.backend import wait_until

from .inspection import require_unlocked

GLOBAL_MENUS = frozenset({"add", "more"})
CONVERSATION_MENUS = frozenset({"recent", "files", "capture", "applications"})


class NavigationUI:
    def __init__(self, client):
        self.client = client
        self.ui = client.control_ui
        self.b = client.backend

    def _menu(self):
        return wait_until(
            lambda: self.b.find(
                self.b.window, lambda x: x.ControlTypeName == "MenuControl", False
            )
        )

    def _read(self, menu):
        return wait_until(lambda: self.ui._actions(menu))

    def global_menu(self, area):
        if area not in GLOBAL_MENUS:
            raise ValueError("Account menu must be add or more")
        with ui_transaction():
            self.b.check_thread()
            require_unlocked(self.b.auto)
            previous = self.b.window
            self.b.use_main_window()
            try:
                self.b.window.SetActive(waitTime=0.05)
                if area == "add":
                    button = self.ui.by_class("contact-adder-btn", required=True)
                    pattern = button.GetPattern(
                        self.b.auto.PatternId.ExpandCollapsePattern
                    )
                    if pattern is None:
                        raise ControlUnavailable(
                            "Add menu has no ExpandCollapsePattern"
                        )
                    pattern.Expand(waitTime=0.1)
                else:
                    button = self.ui.by_class("func-menu-more-component", required=True)
                    focusable = [
                        x for x in self.b.walk(button) if x.IsKeyboardFocusable
                    ]
                    if len(focusable) != 1:
                        raise ControlUnavailable(
                            "More menu keyboard target is missing or ambiguous"
                        )
                    self.b.focus_control(focusable[0])
                    self.b.auto.SendKeys("{Enter}", waitTime=0.1)
                return self._read(self._menu())
            finally:
                try:
                    self.ui._dismiss_menu()
                finally:
                    self.b.window = previous

    @contextmanager
    def _settings_page(self, title):
        """Own one temporary settings window and restore its remembered page."""
        from quitbot_backend._engine.models import text_of

        with ui_transaction():
            self.b.check_thread()
            require_unlocked(self.b.auto)
            previous, window = self.b.window, None
            original_page, opened_menu = None, False
            self.b.use_main_window()
            try:
                self.b._require_no_modal()
                pid = self.b.main_window.ProcessId
                existing = self.b.auto.GetRootControl().GetChildren()
                if any(w.ProcessId == pid and w.Name == "设置" for w in existing):
                    raise ControlUnavailable("Existing QQ settings window retained")
                if self.b.find(self.b.window, lambda c: c.ControlTypeName == "MenuControl", False):
                    raise ControlUnavailable("Existing QQ menu retained")
                handles = {w.NativeWindowHandle for w in existing}
                trigger = self.ui.by_class("func-menu-more-component", required=True)
                focusable = [c for c in self.b.walk(trigger) if c.IsKeyboardFocusable]
                if len(focusable) != 1:
                    raise ControlUnavailable("More menu keyboard target is ambiguous")
                self.b.focus_control(focusable[0])
                self.b.auto.SendKeys("{Enter}", waitTime=0.1)
                opened_menu = True
                self.b.invoke(self.ui._menu_item(self._menu(), "设置"))
                windows = wait_until(lambda: [
                    w for w in self.b.auto.GetRootControl().GetChildren()
                    if w.ProcessId == pid and w.Name == "设置" and w.NativeWindowHandle not in handles
                ])
                if len(windows) != 1:
                    raise ControlUnavailable("New QQ settings window is ambiguous")
                window = windows[0]

                def page_item(name):
                    items = [c for c in self.b.walk(window)
                             if "nav-item" in c.ClassName.split() and text_of(self.b.snapshot(c)) == name]
                    if len(items) != 1:
                        raise ControlUnavailable("Settings page is missing or ambiguous")
                    return items[0]

                active = [text_of(self.b.snapshot(c)) for c in self.b.walk(window)
                          if "nav-item-active" in c.ClassName.split()]
                if len(active) != 1:
                    raise ControlUnavailable("Original settings page is ambiguous")
                original_page = active[0]
                self.b.invoke(page_item(title))
                wait_until(lambda: "nav-item-active" in page_item(title).ClassName.split())
                yield window
            finally:
                try:
                    if window is not None:
                        try:
                            if original_page and original_page != title:
                                self.b.invoke(page_item(original_page))
                                wait_until(lambda: "nav-item-active" in page_item(original_page).ClassName.split())
                        finally:
                            window.GetWindowPattern().Close(waitTime=0.1)
                    if opened_menu:
                        self.ui._dismiss_menu()
                finally:
                    self.b.window = previous

    def client_version(self):
        """Return the visible version verbatim, without guessing build fields."""
        with self._settings_page("关于QQ") as window:
            def read():
                values = [c.Name.removeprefix("版本:").strip() for c in self.b.walk(window)
                          if c.ControlTypeName == "TextControl" and c.Name.startswith("版本:")]
                if len(values) > 1:
                    raise ControlUnavailable("QQ version text is ambiguous")
                return values[0] if values else None

            return wait_until(read)

    def storage_paths(self):
        """Read displayed paths only; never open QQ's data directory or database."""
        from pathlib import PurePosixPath, PureWindowsPath

        from quitbot_backend._engine.models import text_of

        with self._settings_page("存储管理") as window:
            def read():
                area = self.ui.by_class("location-setting", window)
                if area is None:
                    return None
                result = {"data_directory": None}
                for field, cls in (("download_directory", "file-location-tips"),
                                   ("data_directory", "location-tips")):
                    nodes = [c for c in self.b.walk(area) if cls in c.ClassName.split()]
                    if not nodes and field == "data_directory":
                        continue
                    if len(nodes) != 1:
                        raise ControlUnavailable("Storage path is missing or ambiguous")
                    value = text_of(self.b.snapshot(nodes[0]))
                    absolute = PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()
                    if not absolute or value.endswith(("…", "...")):
                        raise ControlUnavailable("Displayed storage path is not a complete absolute path")
                    result[field] = value
                return result

            return wait_until(read)

    def _recent(self, target):
        self.b.use_main_window()
        self.b.open_session(target.name)
        self.b.profile(target.id, target.name, target.kind)
        row = self._recent_row(target)
        focusable = [x for x in self.b.walk(row) if x.IsKeyboardFocusable]
        if len(focusable) != 1:
            raise ControlUnavailable("Recent conversation keyboard target is ambiguous")
        return self.ui._context(focusable[0])

    def _recent_row(self, target):
        pane = self.b.find(
            self.b.window,
            lambda x: x.ControlTypeName == "PaneControl" and x.Name == "会话列表",
        )
        rows = [
            x
            for x in self.b.walk(pane)
            if "recent-contact-item" in x.ClassName.split()
            and self.b.contact_name(x) == target.name
        ]
        if len(rows) != 1:
            raise TargetMismatch("Recent conversation is missing or ambiguous")
        return rows[0]

    def mark_unread(self, key):
        with self.ui.checked(key) as target:
            menu = self._recent(target)
            submitted = False
            try:
                leaf = self.ui._menu_item(menu, "标记未读")
                if not leaf.IsEnabled:
                    raise ControlUnavailable("Mark unread is disabled")
                submitted = True
                self.b.invoke(leaf)

                def unread():
                    import re

                    return any(
                        re.fullmatch(r"\d+条未读", c.Name)
                        for c in self.b.walk(self._recent_row(target))
                    )

                wait_until(unread)
            except Exception as error:
                if submitted:
                    raise MutationOutcomeUnknown(
                        "Unread marker not confirmed; inspect before retrying"
                    ) from error
                raise
            finally:
                self.ui._dismiss_menu()
            return SettingReceipt(target.id, "unread", False, True)

    def conversation_menu(self, key, area):
        if area not in CONVERSATION_MENUS:
            raise ValueError(
                "Conversation menu must be recent/files/capture/applications"
            )
        if area == "applications":
            return self.ui.application_menu(key)
        with self.ui.checked(key) as target:
            previous = self.b.window
            try:
                if area == "recent":
                    menu = self._recent(target)
                else:
                    names = (
                        ("文件弹出菜单",)
                        if area == "files"
                        else ("截图弹出菜单", "截图 Ctrl + Alt + A弹出菜单")
                    )
                    buttons = [
                        x
                        for x in self.b.walk(self.b.window)
                        if x.ControlTypeName == "ButtonControl" and x.Name in names
                    ]
                    if len(buttons) != 1:
                        raise ControlUnavailable(
                            "Toolbar dropdown is missing or ambiguous"
                        )
                    self.b.invoke(buttons[0])
                    menu = self._menu()
                return self._read(menu)
            finally:
                try:
                    self.ui._dismiss_menu()
                finally:
                    self.b.window = previous
