"""Discover loaded recent chats and operate their native context menus."""

import ctypes
import re
import time
from collections import Counter
from contextlib import contextmanager

from ...errors import ControlUnavailable, TargetMismatch
from ...locking import ui_transaction
from ...models import ConversationCandidate, Target
from ...uia import wait_until
from .inspection import require_unlocked


class RecentConversations:
    def __init__(self, backend):
        self.b = backend

    def rows(self):
        pane = self.b.find(
            self.b.window,
            lambda c: c.Name == "会话列表" and c.ControlTypeName == "PaneControl",
        )
        result = []
        for row in self.b.walk(pane):
            if "recent-contact-item" not in row.ClassName.split():
                continue
            title = self.b.find(
                row, lambda c: c.ControlTypeName == "TextControl", False
            )
            if title is not None and title.Name:
                result.append((title.Name, row))
        return result

    def row(self, name):
        matches = [row for label, row in self.rows() if label == name]
        if len(matches) > 1:
            raise TargetMismatch(
                f"会话列表中有多个同名会话：{name}，请使用唯一备注或名称"
            )
        if not matches:
            raise ControlUnavailable(
                f"会话「{name}」未加载，请先在 QQ 中打开，再刷新会话列表"
            )
        return matches[0]

    def locate(self, name):
        """QQ virtualizes the recent list independently of the active chat."""
        try:
            return self.row(name)
        except ControlUnavailable:
            pane = self.b.find(
                self.b.window,
                lambda c: c.Name == "会话列表" and c.ControlTypeName == "PaneControl",
            )
            scroll = pane.GetPattern(self.b.auto.PatternId.ScrollPattern)
            if scroll is None or not scroll.VerticallyScrollable:
                raise
            original = scroll.VerticalScrollPercent
            found = False
            try:
                position = 0.0
                deadline = time.monotonic() + 20
                for _ in range(80):
                    scroll.SetScrollPercent(-1, position, waitTime=0.15)
                    try:
                        result = self.row(name)
                        found = True
                        return result
                    except ControlUnavailable:
                        if position >= 100 or time.monotonic() >= deadline:
                            break
                    # Scroll percentages refer to the scrollable remainder,
                    # while ViewSize refers to the full list. Keep overlap.
                    size = scroll.VerticalViewSize
                    step = max(0.5, min(100, 80 * size / max(1, 100 - size)))
                    position = min(100, position + step)
                raise ControlUnavailable(
                    f"未在 QQ 最近会话列表中找到「{name}」，请检查会话名称"
                )
            finally:
                if not found:
                    scroll.SetScrollPercent(-1, original, waitTime=0.05)

    def discover(self):
        with ui_transaction():
            self.b.check_thread()
            require_unlocked(self.b.auto)
            previous = self.b.window
            try:
                self.b.use_main_window()
                self.b._require_no_modal()
                self.b.navigate("消息")
                names = Counter(name for name, _ in self.rows())
                header = self.b.find(
                    self.b.window,
                    lambda c: "chat-header__contact-name" in c.ClassName.split(),
                    False,
                )
                if header is not None and header.Name and header.Name not in names:
                    names[header.Name] = 1
                return [
                    ConversationCandidate(
                        name,
                        count == 1,
                        "存在同名会话，请先设置唯一名称" if count > 1 else None,
                    )
                    for name, count in names.items()
                ]
            finally:
                self.b.window = previous

    def visible_menu(self):
        # Chromium may expose the popup as a separate top-level window.
        roots = [self.b.window]
        roots.extend(
            w
            for w in self.b.auto.GetRootControl().GetChildren()
            if w.ProcessId == self.b.window.ProcessId
            and w.NativeWindowHandle != self.b.window.NativeWindowHandle
            and w.ControlTypeName == "MenuControl"
        )
        menus = []
        for root in roots:
            menu = self.b.find(
                root,
                lambda c: c.ControlTypeName == "MenuControl" and not c.IsOffscreen,
                False,
            )
            if menu is not None:
                menus.append(menu)
        if len(menus) > 1:
            raise ControlUnavailable("QQ 同时存在多个菜单，请先关闭后再操作")
        return menus[0] if menus else None

    @staticmethod
    def show_native(control):
        from comtypes.gen.UIAutomationClient import IUIAutomationElement3

        control.Element.QueryInterface(IUIAutomationElement3).ShowContextMenu()

    @contextmanager
    def menu(self, name):
        b = self.b
        b.check_thread()
        require_unlocked(b.auto)
        b.use_main_window()
        b._require_no_modal()
        b.navigate("消息")
        if self.visible_menu() is not None:
            raise ControlUnavailable("请先关闭 QQ 中已打开的菜单")
        self.locate(name)

        def content():
            row = self.row(name)
            matches = [c for c in b.walk(row) if c.IsKeyboardFocusable]
            if len(matches) != 1:
                raise ControlUnavailable(f"会话「{name}」的可聚焦控件缺失或不唯一")
            return matches[0]

        # Focusing can replace Chromium's accessibility element. Reacquire it.
        b.focus_control(content(), resolve=content)
        opened = False
        try:
            opened = True
            b.auto.SendKeys("{Shift}{F10}", waitTime=0.05)
            try:
                menu = wait_until(self.visible_menu, timeout=2)
            except ControlUnavailable:
                # Retry only opening a menu, and only if no popup appeared.
                # Never replay the selected menu action.
                menu = self.visible_menu()
                if menu is None:
                    self.show_native(content())
                    menu = wait_until(self.visible_menu, timeout=3)

            def loaded_menu():
                current = self.visible_menu()
                if current is not None and any(
                    c.ControlTypeName == "MenuItemControl" and not c.IsOffscreen
                    for c in b.walk(current)
                ):
                    return current
                return None

            yield wait_until(loaded_menu, timeout=3)
        finally:
            if opened and self.visible_menu() is not None:
                require_unlocked(b.auto)
                b.window.SetActive(waitTime=0.05)
                b.auto.SendKeys("{Esc}", waitTime=0.05)
                wait_until(self.visible_menu, lambda value: value is None, timeout=2)

    def resolve(self, name):
        """Read only the selected chat's number via QQ's Copy Number action."""
        if not name.strip():
            raise ValueError("会话名称不能为空")
        with ui_transaction():
            previous = self.b.window
            try:
                with self.menu(name) as menu:
                    kinds = {
                        "复制群号": "group",
                        "复制QQ号": "private",
                        "复制 QQ 号": "private",
                    }
                    matches = [
                        c
                        for c in self.b.walk(menu)
                        if c.ControlTypeName == "MenuItemControl"
                        and c.Name in kinds
                        and not c.IsOffscreen
                    ]
                    if len(matches) != 1:
                        raise ControlUnavailable(
                            "此会话没有唯一的复制群号或 QQ 号入口，可手动填写监听范围"
                        )
                    item = matches[0]
                    kind = kinds[item.Name]
                    sequence = ctypes.windll.user32.GetClipboardSequenceNumber()
                    self.b.invoke(item)
                    copied_sequence = wait_until(
                        lambda: ctypes.windll.user32.GetClipboardSequenceNumber(),
                        lambda value: value != sequence,
                        timeout=3,
                    )
                    number = self.b.auto.GetClipboardText().strip()
                    if (
                        ctypes.windll.user32.GetClipboardSequenceNumber()
                        != copied_sequence
                        or not re.fullmatch(r"[1-9][0-9]{4,11}", number)
                    ):
                        raise ControlUnavailable(
                            "QQ 未返回有效号码，或复制期间剪贴板已变化"
                        )
                    self.row(name)  # Recheck uniqueness after the asynchronous copy.
                    return Target(number, name, kind)
            finally:
                self.b.window = previous
