"""Shared QQ control traversal, account binding and verified UI operations."""

import re
import threading
import time
from dataclasses import dataclass

from ..errors import ControlUnavailable, FocusLost, TargetMismatch
from ..models import Node, Target, editor_has_content, parse_search_results, text_of


def validate_group_code(texts, expected_id):
    numbers = {t.strip() for t in texts if re.fullmatch(r"\d{5,12}", t.strip())}
    if numbers != {str(expected_id)}:
        raise TargetMismatch(
            "Visible group-code field does not match the configured group"
        )
    return tuple(texts)


@dataclass(frozen=True)
class DetachedWindow:
    target_id: str
    name: str
    hwnd: int
    pid: int


def navigation_label_matches(label, name):
    return (
        label == name
        or re.fullmatch(re.escape(name) + r" \d+\+?(?:条未读|个通知)", label)
        is not None
    )


def is_transient_control_error(error):
    from ..platforms import is_transient_error

    return is_transient_error(error)


def wait_until(read, predicate=bool, timeout=8.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = read()
        except Exception as error:
            # Only retry provider reads whose control disappeared while QQ
            # rebuilt its tree. Never replay an input action here.
            if not is_transient_control_error(error):
                raise
            time.sleep(0.15)
            continue
        if predicate(last):
            return last
        time.sleep(0.15)
    raise ControlUnavailable(f"UI state did not settle within {timeout}s")


class DesktopBackend:
    def __init__(self, account: str, hwnd: int | None = None):
        from ..platforms import automation

        self.auto = automation()
        self.thread_id = threading.get_ident()
        self.account = account
        candidates = []
        for window in self.windows():
            if hwnd is not None and window.NativeWindowHandle != hwnd:
                continue
            avatar = self.find(
                window,
                lambda c: (
                    c.ControlTypeName == "ButtonControl"
                    and "user-avatar" in c.ClassName.split()
                ),
                required=False,
            )
            if avatar is not None and avatar.Name == f"{account}的头像":
                candidates.append(window)
        if len(candidates) != 1:
            raise ControlUnavailable(
                f"Expected exactly one QQ window for {account!r}, found {len(candidates)}"
            )
        self.window = candidates[0]
        self.main_window = self.window
        self._detached = {}
        self.hwnd = self.window.NativeWindowHandle

    def use_main_window(self):
        self.window = getattr(self, "main_window", self.window)

    def chat_windows(self, target):
        main = getattr(self, "main_window", self.window)
        result = []
        for window in self.auto.GetRootControl().GetChildren():
            if (
                window.ProcessId != main.ProcessId
                or window.ClassName != "Chrome_WidgetWin_1"
                or window.NativeWindowHandle == main.NativeWindowHandle
            ):
                continue
            header = self.find(
                window,
                lambda x: "chat-header__contact-name" in x.ClassName.split(),
                False,
            )
            if header is not None and header.Name == target.name:
                result.append(window)
        return result

    def activate_detached(self, target, *, required=False):
        cached = getattr(self, "_detached", {}).get(target.id)
        matches = self.chat_windows(target)
        if cached:
            matches = [w for w in matches if w.NativeWindowHandle == cached.hwnd]
        if len(matches) > 1:
            details = [
                {
                    "hwnd": getattr(w, "NativeWindowHandle", None),
                    "pid": getattr(w, "ProcessId", None),
                    "title": getattr(w, "Name", "")[:160],
                }
                for w in matches[:8]
            ]
            raise TargetMismatch(
                f"Ambiguous detached chat windows for target {target.id!r}: "
                f"{len(matches)} matches, windows={details!r}, "
                f"cached_hwnd={cached.hwnd if cached else None}"
            )
        if not matches:
            if required or cached:
                raise ControlUnavailable(
                    "Detached chat closed or replaced; explicitly reopen it"
                )
            return False
        self.window = matches[0]
        return True

    def refresh_detached(self, target):
        """Foreground one verified chat so QQ can update its rendered UI tree.

        The caller must hold ui_transaction. This intentionally changes focus
        within this desktop session; ordinary detached reads never call it.
        """
        from ..ui.workflows.inspection import require_unlocked

        self.check_thread()
        cached = self._detached.get(target.id)
        if cached is None:
            raise ControlUnavailable("Open and verify the detached chat before refresh")
        require_unlocked(self.auto)
        self.activate_detached(target, required=True)
        if (
            self.window.NativeWindowHandle != cached.hwnd
            or self.window.ProcessId != cached.pid
            or self.main_window.ProcessId != cached.pid
            or self.header().Name != target.name
        ):
            raise TargetMismatch("Detached chat binding changed before refresh")
        self.window.SetActive(waitTime=0.15)

        def foreground_matches():
            foreground = self.auto.GetForegroundControl()
            return (
                foreground is not None
                and foreground.NativeWindowHandle == cached.hwnd
                and foreground.ProcessId == cached.pid
                and self.header().Name == target.name
            )

        wait_until(foreground_matches, timeout=3)

    def open_detached(self, target):
        from ..ui.workflows.recent import RecentConversations

        self.check_thread()
        # Explicit reopen may replace a previously closed handle. Polling never
        # silently changes the verified binding to a newly created window.
        if target.id in self._detached:
            cached = self._detached[target.id]
            if not any(
                w.NativeWindowHandle == cached.hwnd for w in self.chat_windows(target)
            ):
                del self._detached[target.id]
        stage = "查找独立窗口"
        try:
            active = self.activate_detached(target)
            if active and target.id in self._detached:
                return self._detached[target.id]
            if not active:
                stage = "打开并核验会话"
                self.use_main_window()
                self.window.SetActive(waitTime=0.1)
                self.open_target(target)
                self.profile(target.id, target.name, target.kind)
                # QQ's own window preference can already have created a chat
                # window while opening the contact. Do not detach it a second time.
                if not self.activate_detached(target):
                    stage = "打开会话右键菜单"
                    recent = RecentConversations(self)
                    with recent.menu(target.name) as menu:
                        stage = "调用打开独立聊天窗口"
                        items = [
                            c
                            for c in self.walk(menu)
                            if c.ControlTypeName == "MenuItemControl"
                            and c.Name == "打开独立聊天窗口"
                            and not c.IsOffscreen
                        ]
                        if len(items) != 1:
                            raise ControlUnavailable(
                                "当前会话菜单中没有唯一的「打开独立聊天窗口」"
                            )
                        self.invoke(items[0])
                    stage = "等待独立窗口和聊天标题加载"
                    wait_until(lambda: self.activate_detached(target))
            stage = "核验独立窗口的会话号码"
            self.profile(target.id, target.name, target.kind)
            # A profile fallback may temporarily route via the main window.
            self.activate_detached(target, required=True)
            info = DetachedWindow(
                target.id,
                target.name,
                self.window.NativeWindowHandle,
                self.window.ProcessId,
            )
            self._detached[target.id] = info
            return info
        except (ControlUnavailable, FocusLost, TargetMismatch) as error:
            raise type(error)(
                f"会话「{target.name}」({target.id})：{stage}失败：{error}"
            ) from error

    def check_thread(self):
        if threading.get_ident() != self.thread_id:
            raise RuntimeError(
                "Create and use QQClient on the same desktop worker thread"
            )

    def windows(self):
        return [
            w
            for w in self.auto.GetRootControl().GetChildren()
            if w.Name == "QQ" and w.ClassName == "Chrome_WidgetWin_1"
        ]

    def walk(self, root):
        yield root
        for control, _ in self.auto.WalkControl(root, maxDepth=35):
            yield control

    def find(self, root, predicate, required=True):
        for attempt in range(2):
            try:
                for control in self.walk(root):
                    if predicate(control):
                        return control
                break
            except Exception as error:
                if attempt or not is_transient_control_error(error):
                    raise
                time.sleep(0.1)
        if required:
            raise ControlUnavailable("Expected QQ UI Automation control is unavailable")
        return None

    def by_class(self, name, root=None):
        return self.find(
            root if root is not None else self.window,
            lambda c: name in c.ClassName.split(),
        )

    def snapshot(self, control, depth=0):
        if depth > 35:
            raise ControlUnavailable("Unexpected UIA tree depth")
        try:
            return Node(
                control.ControlTypeName,
                control.Name,
                control.ClassName,
                control.AutomationId,
                tuple(self.snapshot(c, depth + 1) for c in control.GetChildren()),
            )
        except Exception as error:
            if depth or not is_transient_control_error(error):
                raise
            time.sleep(0.1)
            return self.snapshot(control, depth=1)

    def invoke(self, control):
        self.check_thread()
        if not control.IsEnabled:
            raise ControlUnavailable(f"Control disabled: {control.Name}")
        pattern = control.GetPattern(self.auto.PatternId.InvokePattern)
        if pattern:
            pattern.Invoke(waitTime=0.05)
            return "InvokePattern.Invoke"
        selection = control.GetPattern(self.auto.PatternId.SelectionItemPattern)
        if selection:
            selection.Select(waitTime=0.05)
            return "SelectionItemPattern.Select"
        toggle = control.GetPattern(self.auto.PatternId.TogglePattern)
        if toggle:
            toggle.Toggle(waitTime=0.05)
            return "TogglePattern.Toggle"
        legacy = control.GetLegacyIAccessiblePattern()
        if legacy and legacy.DefaultAction:
            legacy.DoDefaultAction(waitTime=0.05)
            return "LegacyIAccessible.DoDefaultAction"
        raise ControlUnavailable(f"No control action pattern: {control.Name}")

    def header(self):
        return self.by_class("chat-header__contact-name")

    def editor(self):
        return self.by_class("ExEditor-qq-msg-editor")

    def focus_editor(self):
        # QQ can replace the accessibility element as a conversation's editor
        # gains focus. Re-read that unique composer instead of waiting on the
        # stale element, without replaying any keyboard input.
        self.window.SetActive(waitTime=0.05)
        self.editor().SetFocus()

        def focused_editor():
            latest = self.editor()
            current = self.auto.GetFocusedControl()
            return (
                latest
                if current is not None and self.auto.ControlsAreSame(current, latest)
                else None
            )

        editor = wait_until(focused_editor, timeout=3)
        self.ensure_focus(editor)
        return editor

    def focus_control(self, editor, *, resolve=None):
        self.window.SetActive(waitTime=0.05)
        editor.SetFocus()

        def verified():
            expected = resolve() if resolve is not None else editor
            current = self.auto.GetFocusedControl()
            return (
                expected
                if current is not None and self.auto.ControlsAreSame(current, expected)
                else None
            )

        latest = wait_until(verified, timeout=2)
        self.ensure_focus(latest)
        return latest

    def ensure_focus(self, expected):
        current = self.auto.GetFocusedControl()
        if not current or not self.auto.ControlsAreSame(current, expected):
            raise FocusLost("QQ editor lost focus; input stopped")

    def type_literal(self, editor, text):
        """Insert literal text using the selected platform's verified input path."""
        self.check_thread()
        self.auto.type_text(editor, text)

    def open_session(self, name):
        self.check_thread()
        self.navigate("消息")
        header = self.find(
            self.window,
            lambda c: "chat-header__contact-name" in c.ClassName.split(),
            required=False,
        )
        if header is not None and header.Name == name:
            return
        sessions = self.find(
            self.window,
            lambda c: c.Name == "会话列表" and c.ControlTypeName == "PaneControl",
        )
        matches = []
        for c in self.walk(sessions):
            if "recent-contact-item" not in c.ClassName.split():
                continue
            title = self.find(
                c, lambda n: n.ControlTypeName == "TextControl", required=False
            )
            if title and title.Name == name:
                matches.append(c)
        if len(matches) > 1:
            raise TargetMismatch(f"More than one recent session named {name!r}")
        if not matches:
            raise ControlUnavailable(
                f"Expected one loaded session named {name!r}, found {len(matches)}; open it once in QQ"
            )
        self.invoke(matches[0])

        def loaded_header():
            current = self.find(
                self.window,
                lambda c: "chat-header__contact-name" in c.ClassName.split(),
                required=False,
            )
            return current.Name if current is not None else None

        wait_until(loaded_header, lambda value: value == name)

    def navigate(self, name):
        self.use_main_window()
        marker = {"消息": "会话列表", "联系人": "联系人列表"}.get(name)

        def visible_view():
            if marker is None:
                return None
            return self.find(
                self.window,
                lambda x: (
                    x.ControlTypeName == "PaneControl"
                    and x.Name == marker
                    and not x.IsOffscreen
                ),
                required=False,
            )

        if visible_view() is not None:
            return
        nav = self.find(
            self.window,
            lambda c: (
                navigation_label_matches(c.Name, name)
                and "nav-item" in c.ClassName.split()
            ),
        )
        pattern = nav.GetPattern(self.auto.PatternId.TogglePattern)
        if pattern is None:
            raise ControlUnavailable(f"Navigation lacks TogglePattern: {name}")
        if marker is not None or pattern.ToggleState != 1:
            pattern.Toggle(waitTime=0.1)
            if marker is not None:
                wait_until(visible_view)
                return

            # Chromium can replace a navigation node during the transition.
            # Reacquire the pattern instead of polling the detached old node.
            def current_state():
                current = self.find(
                    self.window,
                    lambda c: (
                        navigation_label_matches(c.Name, name)
                        and "nav-item" in c.ClassName.split()
                    ),
                )
                return current.GetPattern(self.auto.PatternId.TogglePattern).ToggleState

            wait_until(current_state, lambda state: state == 1)

    def _require_no_modal(self, window=None):
        if hasattr(self.auto, "find_file_picker") and self.auto.find_file_picker(
            self.window if window is None else window
        ) is not None:
            raise ControlUnavailable("Close the existing native file picker first")
        dialog = self.find(
            self.window if window is None else window,
            lambda c: (
                (
                    "q-dialog-main" in c.ClassName.split()
                    or c.ControlTypeName == "WindowControl"
                    and c.ClassName == "#32770"
                )
                and not c.IsOffscreen
            ),
            required=False,
        )
        if dialog is not None:
            raise ControlUnavailable(
                "Handle the existing QQ dialog before navigating or opening a profile"
            )

    def open_target(self, target, route="auto"):
        if route not in ("auto", "recent", "contacts"):
            raise ValueError("route must be auto, recent, or contacts")
        self._require_no_modal()
        main = getattr(self, "main_window", self.window)
        if main is not self.window:
            self._require_no_modal(main)
        if hasattr(self, "main_window"):
            if route != "contacts" and self.activate_detached(target):
                return
            self.use_main_window()
        if route != "contacts":
            try:
                self.open_session(target.name)
                return
            except ControlUnavailable:
                if route == "recent":
                    raise
        self.open_from_contacts(target)

    def open_from_contacts(self, target):
        """Select an address-book row, verify its profile, then invoke chat."""
        self.check_thread()
        expanded = []
        try:
            self.navigate("联系人")
            tab_name = "群聊" if target.kind == "group" else "好友"
            tab = wait_until(
                lambda: self.find(
                    self.window,
                    lambda c: (
                        c.ControlTypeName == "TabItemControl" and c.Name == tab_name
                    ),
                    required=False,
                )
            )
            self.invoke(tab)
            pane = self.contacts_pane(target.kind)
            for control in list(self.walk(pane)):
                if "q-collapse-item__header" not in control.ClassName.split():
                    continue
                pattern = control.GetPattern(self.auto.PatternId.ExpandCollapsePattern)
                if pattern and pattern.ExpandCollapseState == 0:
                    pattern.Expand(waitTime=0.05)
                    expanded.append(pattern)

            def loaded_rows():
                current = self.contacts_pane(target.kind)
                return [
                    c
                    for c in self.walk(current)
                    if "contact-item" in c.ClassName.split()
                ]

            rows = wait_until(loaded_rows, timeout=3)
            matches = []
            for control in rows:
                first = self.find(
                    control,
                    lambda c: c.ControlTypeName == "TextControl",
                    required=False,
                )
                if first is not None and first.Name == target.name:
                    matches.append(control)
            if not matches:
                # QQ virtualizes large address books and remembers their scroll
                # position. Search bounded scroll positions using native UIA.
                pane = self.find(
                    self.window,
                    lambda c: c.Name == "联系人列表" and c.ControlTypeName == "PaneControl",
                )
                native_scroll = hasattr(pane, "scroll_pages")
                positions = pane.scroll_pages() if native_scroll else (0, 20, 40, 60, 80, 100)
                for percent in positions:
                    if not native_scroll:
                        scroll = pane.GetPattern(self.auto.PatternId.ScrollPattern)
                        if scroll is None or not scroll.VerticallyScrollable:
                            break
                        scroll.SetScrollPercent(-1, percent, waitTime=0.2)
                    for control in self.walk(pane):
                        if "contact-item" not in control.ClassName.split():
                            continue
                        first = self.find(
                            control,
                            lambda c: c.ControlTypeName == "TextControl",
                            required=False,
                        )
                        if first is not None and first.Name == target.name:
                            matches.append(control)
                    if matches:
                        break
            if len(matches) != 1:
                raise TargetMismatch(
                    f"Expected one loaded contact named {target.name!r}, found {len(matches)}"
                )
            row = matches[0]

            def selected_id():
                id_class = (
                    "profile__info-uin"
                    if target.kind == "group"
                    else "buddy-profile__info-uin"
                )
                control = self.find(
                    self.window,
                    lambda c: id_class in c.ClassName.split(),
                    required=False,
                )
                return text_of(self.snapshot(control)) if control is not None else ""

            identity = self.select_contact_details(row, target, selected_id)
            button = (
                self.by_class("profile__contact-chat")
                if target.kind == "group"
                else self.find(
                    self.window,
                    lambda c: (
                        c.ControlTypeName == "ButtonControl" and c.Name == "发消息"
                    ),
                )
            )
            self.invoke(button)
            self.navigate("消息")

            def routed_header():
                if hasattr(self, "main_window"):
                    self.activate_detached(target)
                header = self.find(
                    self.window,
                    lambda c: "chat-header__contact-name" in c.ClassName.split(),
                    required=False,
                )
                return header.Name if header is not None else None

            wait_until(routed_header, lambda value: value == target.name)
            return (identity,)
        finally:
            if expanded:
                self.navigate("联系人")
                for pattern in reversed(expanded):
                    pattern.Collapse(waitTime=0.05)
            self.navigate("消息")

            if hasattr(self, "main_window"):
                self.activate_detached(target)

    def select_contact_details(self, row, target, read_id):
        current = read_id()
        if target.id in current.split():
            return current
        toggle = row.GetPattern(self.auto.PatternId.TogglePattern)
        if toggle:
            # QQ can retain selection while remounting an empty detail pane.
            # Reselect once; do not synthesize a double click from a stale flag.
            toggle.Toggle(waitTime=0.1)
        else:
            self.invoke(row)
        return wait_until(read_id, lambda value: target.id in value.split())

    def contacts_pane(self, kind):
        """Wait for the selected address-book tab, not its previous DOM tree."""
        prefixes = (
            ("我创建的群聊", "我管理的群聊", "我加入的群聊", "置顶群聊")
            if kind == "group"
            else ("我的设备", "特别关心", "我的好友")
        )

        def ready():
            pane = self.find(
                self.window,
                lambda c: c.Name == "联系人列表" and c.ControlTypeName == "PaneControl",
                required=False,
            )
            if pane is None:
                return None
            names = [
                c.Name
                for c in self.walk(pane)
                if "q-collapse-item__header" in c.ClassName.split()
            ]
            return (
                pane
                if any(
                    name.startswith(prefix + " ")
                    for name in names
                    for prefix in prefixes
                )
                else None
            )

        return wait_until(ready)

    def search(self, query, limit=30):
        self.check_thread()
        self.navigate("消息")
        area = self.by_class("main-search__search-input")
        edit = self.find(area, lambda c: c.ControlTypeName == "EditControl")
        pattern = edit.GetValuePattern()
        previous = pattern.Value
        try:
            self.window.SetActive(waitTime=0.05)
            edit.SetFocus()
            wait_until(
                lambda: self.auto.GetFocusedControl(),
                lambda current: (
                    current is not None and self.auto.ControlsAreSame(current, edit)
                ),
                timeout=2,
            )
            pattern.SetValue(query, waitTime=0.8)

            def read_results():
                pane = self.find(
                    self.window,
                    lambda c: "search-content-scroll" in c.ClassName.split(),
                    required=False,
                )
                return (
                    parse_search_results(self.snapshot(pane))
                    if pane is not None
                    else []
                )

            first = read_results()
            time.sleep(0.2)
            second = read_results()
            if first != second:
                time.sleep(0.3)
                second = read_results()
            return second[:limit]
        finally:
            pattern.SetValue(previous, waitTime=0.1)
            edit.SetFocus()
            self.ensure_focus(edit)
            self.auto.SendKeys("{Esc}", waitTime=0.05)

    def profile(self, expected_id, expected_name, kind=None):
        from ..ui.workflows.inspection import require_unlocked
        from ..ui.workflows.profile_cards import ProfileCards

        require_unlocked(self.auto)
        self._require_no_modal()
        # Chromium profile popovers may not open for an inactive QQ window.
        self.window.SetActive(waitTime=0.1)
        if self.header().Name != expected_name:
            raise TargetMismatch("Conversation changed before profile lookup")
        if kind == "group":
            return self._group_profile(expected_id, expected_name)
        probe = ProfileCards(self)
        # Some QQ builds ignore Invoke on private-chat headers. Focusing the
        # actual UIA button and activating it with Enter works for both kinds.
        header = self.header()
        self.focus_control(header, resolve=self.header)
        self.auto.SendKeys("{Enter}", waitTime=0.1)
        try:
            result = wait_until(
                lambda: probe.refreshed(
                    lambda xs: any(expected_id in x.split() for x in xs)
                ),
                timeout=2,
            )
        except ControlUnavailable:
            # The floating card is unreliable on some private-chat transitions.
            # The address-book detail panel exposes the same numeric identity
            # through UIA and can verify it before reopening this conversation.
            if kind is None:
                members = self.find(
                    self.window,
                    lambda c: (
                        c.Name == "群成员列表" and c.ControlTypeName == "PaneControl"
                    ),
                    required=False,
                )
                kind = "group" if members is not None else "private"
            target = Target(expected_id, expected_name, kind)
            return self.open_from_contacts(target)
        card = probe.reveal(result)
        try:
            return result[2]
        finally:
            pattern = card.GetWindowPattern()
            if pattern:
                pattern.Close(waitTime=0.3)
            else:
                raise ControlUnavailable("QQ profile card has no WindowPattern.Close")

    def _group_profile(self, expected_id, expected_name):
        """Read the active group's explicitly labelled code in its settings pane."""

        def panel():
            return self.find(
                self.window,
                lambda c: "side-panel" in c.ClassName.split() and not c.IsOffscreen,
                False,
            )

        def code():
            root = panel()
            return (
                self.find(
                    root,
                    lambda c: (
                        "group-profile-card__groupcode-wrap" in c.ClassName.split()
                        and not c.IsOffscreen
                    ),
                    False,
                )
                if root is not None
                else None
            )

        def more():
            return self.find(
                self.header().GetParentControl().GetParentControl(),
                lambda c: c.ControlTypeName == "ButtonControl" and c.Name == "更多",
            )

        opened = False
        if code() is None:
            if (
                self.find(
                    self.window, lambda c: "q-dialog-main" in c.ClassName.split(), False
                )
                is not None
            ):
                raise ControlUnavailable(
                    "Existing modal retained before group verification"
                )
            if panel() is not None:
                if editor_has_content(self.snapshot(self.editor())):
                    raise ControlUnavailable(
                        "Existing draft/panel retained before group verification"
                    )
                self.focus_control(more(), resolve=more)
                self.auto.SendKeys("{Esc}", waitTime=0.1)
                wait_until(panel, lambda value: value is None)
            self.focus_control(more(), resolve=more)
            self.auto.SendKeys("{Enter}", waitTime=0.1)
            opened = True
        try:
            current = wait_until(code, timeout=3)
            fields = [
                x.Name
                for x in self.walk(current)
                if x.ControlTypeName == "TextControl" and x.Name
            ]
            result = validate_group_code(fields, expected_id)
            if self.header().Name != expected_name:
                raise TargetMismatch("Conversation changed while reading group code")
            return result
        finally:
            if opened and panel() is not None:
                from ..ui.workflows.inspection import require_unlocked

                require_unlocked(self.auto)
                if self.header().Name != expected_name:
                    raise TargetMismatch(
                        "Conversation changed before closing group verification panel"
                    )
                self.focus_control(more(), resolve=more)
                self.auto.SendKeys("{Enter}", waitTime=0.1)
                wait_until(panel, lambda value: value is None)
