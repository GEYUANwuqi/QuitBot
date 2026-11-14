"""AT-SPI controls used by the shared QQ semantic workflows.

The small UIA-shaped surface is internal: actions are advertised by AT-SPI,
window identity comes from X11, and unsupported patterns remain absent.
"""

import time
from dataclasses import dataclass
from types import SimpleNamespace

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi, GLib

from ...errors import ControlTreeChanged, ControlUnavailable, FocusLost
from .snapshot import ROLE_TYPES
from .x11 import Desktop

PATTERNS = (
    "Invoke",
    "LegacyIAccessible",
    "Value",
    "Toggle",
    "SelectionItem",
    "ExpandCollapse",
    "ScrollItem",
    "Scroll",
    "Window",
    "Transform",
    "Text",
)
TYPES = {
    **ROLE_TYPES,
    "embedded": "PaneControl",
    "scroll pane": "PaneControl",
    "text": "EditControl",
    "password text": "EditControl",
    "label": "TextControl",
    "check box": "CheckBoxControl",
    "radio button": "RadioButtonControl",
    "menu": "MenuControl",
    "popup menu": "MenuControl",
    "menu item": "MenuItemControl",
    "check menu item": "MenuItemControl",
    "radio menu item": "MenuItemControl",
    "list": "ListControl",
    "list item": "ListItemControl",
    "table": "TableControl",
    "table cell": "DataItemControl",
    "page tab": "TabItemControl",
    "page tab list": "TabControl",
    "combo box": "ComboBoxControl",
    "slider": "SliderControl",
    "tool bar": "ToolBarControl",
    "separator": "SeparatorControl",
    "dialog": "WindowControl",
    "window": "WindowControl",
    "document web": "DocumentControl",
    "document frame": "DocumentControl",
    "tree": "TreeControl",
    "tree item": "TreeItemControl",
    "scroll bar": "ScrollBarControl",
    "link": "HyperlinkControl",
}


@dataclass(frozen=True)
class Rectangle:
    left: int
    top: int
    right: int
    bottom: int

    def width(self):
        return self.right - self.left

    def height(self):
        return self.bottom - self.top


class Control:
    def __init__(self, auto, accessible, window=None):
        self.auto, self.obj, self.native = auto, accessible, window

    def state(self, name):
        self.obj.clear_cache()
        return self.obj.get_state_set().contains(getattr(Atspi.StateType, name))

    @property
    def Name(self):
        self.obj.clear_cache()
        return self.obj.get_name() or ""

    @property
    def ClassName(self):
        # Match the normalized top-level window category used by QQ workflows.
        if self.native:
            return (
                "Chrome_WidgetWin_1"
                if "qq" in self.native.class_name.lower()
                else "#32770"
            )
        return self.obj.get_attributes().get("class", "")

    @property
    def AutomationId(self):
        attributes = self.obj.get_attributes()
        value = attributes.get("id", "")
        if "ml-item" in attributes.get("class", "").split() and not value:
            raise ControlUnavailable("AT-SPI message row has no stable DOM id")
        return value

    @property
    def ControlTypeName(self):
        return TYPES.get(self.obj.get_role_name(), "GroupControl")

    @property
    def ProcessId(self):
        return self.obj.get_process_id()

    @property
    def NativeWindowHandle(self):
        return self.native.id if self.native else 0

    @property
    def IsEnabled(self):
        return self.state("ENABLED") and self.state("SENSITIVE")

    @property
    def IsKeyboardFocusable(self):
        return self.state("FOCUSABLE")

    @property
    def IsOffscreen(self):
        top = self.GetTopLevelControl()
        if (
            top.native
            and not self.auto.desktop.window(top.native.id, top.ProcessId).mapped
        ):
            return True
        return not self.state("SHOWING") or not self.state("VISIBLE")

    @property
    def BoundingRectangle(self):
        rect = Atspi.Component.get_extents(self.obj, Atspi.CoordType.SCREEN)
        return Rectangle(rect.x, rect.y, rect.x + rect.width, rect.y + rect.height)

    def GetChildren(self):
        self.obj.clear_cache()
        count = self.obj.get_child_count()
        if count < 0:
            raise ControlTreeChanged("AT-SPI child count is not available")
        if count > 20000:
            raise ControlUnavailable("AT-SPI child count exceeds the traversal limit")
        result = [self.obj.get_child_at_index(i) for i in range(count)]
        if any(child is None for child in result):
            raise ControlTreeChanged("AT-SPI tree changed during enumeration")
        return [Control(self.auto, child) for child in result]

    def GetParentControl(self):
        parent = self.obj.get_parent()
        if parent is None or parent.get_role_name() in ("application", "desktop frame"):
            return None
        return self.auto.wrap(parent)

    def GetTopLevelControl(self):
        current = self
        for _ in range(64):
            if current.native:
                return current
            parent = current.obj.get_parent()
            if parent is None or parent.get_role_name() in (
                "application",
                "desktop frame",
            ):
                return self.auto.wrap(current.obj, require_window=True)
            current = Control(self.auto, parent)
        raise ControlUnavailable("AT-SPI ancestor depth exceeds limit")

    def GetRuntimeId(self):
        return (self.obj.app.bus_name, self.obj.path)

    def SetActive(self, waitTime=0):
        top = self.GetTopLevelControl()
        self.auto.desktop.activate(top.native.id, top.ProcessId)
        time.sleep(waitTime)

    def SetFocus(self):
        self.GetTopLevelControl()  # Reject stale, unbound frames before input.
        if not Atspi.Component.grab_focus(self.obj):
            raise FocusLost("AT-SPI rejected focus")

    def MoveCursorToMyCenter(self, simulateMove=False):
        rect = self.BoundingRectangle
        return self.auto.SetCursorPos(
            rect.left + rect.width() // 2, rect.top + rect.height() // 2
        )

    def ToBitmap(self):
        rect = self.BoundingRectangle
        return self.auto.desktop.capture(
            (rect.left, rect.top, rect.width(), rect.height())
        )

    def scroll_page(self, direction):
        from ...ui.workflows.inspection import is_descendant

        if direction not in (-1, 1):
            raise ValueError("Scroll direction must be -1 or 1")
        self.auto.require_unlocked()
        self.SetActive()
        rect, top = self.BoundingRectangle, self.GetTopLevelControl().BoundingRectangle
        left, right = max(rect.left, top.left), min(rect.right, top.right)
        upper, lower = max(rect.top, top.top), min(rect.bottom, top.bottom)
        if left >= right or upper >= lower:
            raise ControlUnavailable("Scroll area has no visible rectangle")
        point = ((left + right) // 2, (upper + lower) // 2)
        hit = self.auto.ControlFromPoint(*point)
        if not is_descendant(self.auto, hit, self):
            raise ControlUnavailable("Scroll area is occluded")
        previous = self.auto.GetCursorPos()
        try:
            self.auto.SetCursorPos(*point)
            if not is_descendant(self.auto, self.auto.ControlFromCursor(), self):
                raise ControlUnavailable("Scroll pointer left the verified area")
            self.auto.desktop.run(
                "click", "--repeat", 6, "--delay", 30, 5 if direction > 0 else 4
            )
            time.sleep(0.15)
        finally:
            if self.auto.GetCursorPos() == point:
                self.auto.SetCursorPos(*previous)

    def scroll_pages(self, max_pages=80):
        def signature():
            return tuple(
                (c.AutomationId, c.Name, c.BoundingRectangle.top)
                for c, _ in self.auto.WalkControl(self, maxDepth=5)
                if c.ControlTypeName in ("TextControl", "ListItemControl")
            )

        previous = signature()
        for _ in range(max_pages):
            self.scroll_page(-1)
            current = signature()
            if current == previous:
                break
            previous = current
        yield
        for _ in range(max_pages):
            self.scroll_page(1)
            current = signature()
            if current == previous:
                break
            previous = current
            yield

    def actions(self):
        action = self.obj.get_action_iface()
        return (
            {action.get_action_name(i): i for i in range(action.get_n_actions())}
            if action
            else {}
        )

    def action(self, names, waitTime=0):
        if not self.IsEnabled:
            raise ControlUnavailable("AT-SPI control is disabled")
        self.GetTopLevelControl()
        actions = self.actions()
        for name in names:
            if name in actions:
                if not Atspi.Action.do_action(self.obj, actions[name]):
                    raise ControlUnavailable(f"AT-SPI rejected action {name}")
                time.sleep(waitTime)
                return
        raise ControlUnavailable(f"AT-SPI does not expose any of {names}")

    def show_context_menu(self):
        self.action(("showContextMenu", "show-context-menu"))

    def GetPattern(self, pattern):
        actions = self.actions()
        interfaces = self.obj.get_interfaces()
        allowed = {
            "Invoke": bool(set(actions) & {"click", "press", "activate", "jump"}),
            "LegacyIAccessible": bool(
                set(actions) & {"click", "press", "activate", "clickAncestor"}
            ),
            "Value": "EditableText" in interfaces
            or ("Text" in interfaces and self.state("EDITABLE")),
            "Toggle": self.state("CHECKABLE")
            or self.obj.get_role_name() == "toggle button",
            "SelectionItem": self.state("SELECTABLE") or "select" in actions,
            "ExpandCollapse": self.state("EXPANDABLE"),
            "ScrollItem": "Component" in interfaces,
            "Window": self.native is not None,
            "Transform": self.native is not None,
        }
        if allowed.get(pattern):
            return Pattern(self, pattern)
        if pattern == "Scroll":
            for item, _ in self.auto.WalkControl(self, maxDepth=3):
                if (
                    item.obj.get_role_name() == "scroll bar"
                    and item.state("VERTICAL")
                    and "Value" in item.obj.get_interfaces()
                ):
                    return ScrollPattern(item)
        return None

    def __getattr__(self, name):
        if (
            name.startswith("Get")
            and name.endswith("Pattern")
            and name[3:-7] in PATTERNS
        ):
            return lambda: self.GetPattern(name[3:-7])
        raise AttributeError(name)


class Pattern:
    def __init__(self, control, kind):
        self.control, self.kind = control, kind

    def Invoke(self, waitTime=0):
        self.control.action(("click", "press", "activate", "jump"), waitTime)

    @property
    def DefaultAction(self):
        return next(
            (
                a
                for a in ("click", "press", "activate", "clickAncestor")
                if a in self.control.actions()
            ),
            "",
        )

    def DoDefaultAction(self, waitTime=0):
        self.control.action((self.DefaultAction,), waitTime)

    def Toggle(self, waitTime=0):
        self.control.action(("toggle", "check", "uncheck", "click", "press"), waitTime)

    @property
    def ToggleState(self):
        return (
            2
            if self.control.state("INDETERMINATE")
            else int(self.control.state("CHECKED") or self.control.state("PRESSED"))
        )

    def Select(self, waitTime=0):
        self.control.action(("select", "click", "activate"), waitTime)

    @property
    def ExpandCollapseState(self):
        return int(self.control.state("EXPANDED"))

    def Expand(self, waitTime=0):
        if not self.ExpandCollapseState:
            self.control.action(("expand", "open", "click", "press"), waitTime)

    def Collapse(self, waitTime=0):
        if self.ExpandCollapseState:
            self.control.action(("collapse", "close", "click", "press"), waitTime)

    @property
    def Value(self):
        from .text import text_value

        return text_value(self.control.obj)

    def SetValue(self, value, waitTime=0):
        self.control.GetTopLevelControl()
        if "EditableText" in self.control.obj.get_interfaces():
            if not Atspi.EditableText.set_text_contents(self.control.obj, value):
                raise ControlUnavailable("AT-SPI rejected text replacement")
        else:
            from ...ui.backend import wait_until

            auto = self.control.auto
            self.control.SetActive()
            self.control.SetFocus()
            wait_until(
                lambda: auto.ControlsAreSame(auto.GetFocusedControl(), self.control),
                timeout=2,
            )
            auto.SendKeys("{Ctrl}a")
            if not auto.ControlsAreSame(auto.GetFocusedControl(), self.control):
                raise FocusLost("Text field lost focus before replacement")
            if value:
                auto.type_text(self.control, value)
            else:
                auto.SendKeys("{Back}")
            wait_until(lambda: self.Value == value, timeout=3)
        time.sleep(waitTime)

    def ScrollIntoView(self, waitTime=0):
        if not Atspi.Component.scroll_to(self.control.obj, Atspi.ScrollType.ANYWHERE):
            raise ControlUnavailable("AT-SPI rejected scrolling")
        time.sleep(waitTime)

    def Close(self, waitTime=0):
        self.control.auto.desktop.close(
            self.control.NativeWindowHandle, self.control.ProcessId
        )
        time.sleep(waitTime)

    def Move(self, x, y, waitTime=0):
        self.control.auto.desktop.move(
            self.control.NativeWindowHandle, self.control.ProcessId, x, y
        )
        time.sleep(waitTime)


class ScrollPattern:
    def __init__(self, scrollbar):
        self.bar = scrollbar.obj

    @property
    def VerticallyScrollable(self):
        return Atspi.Value.get_maximum_value(self.bar) > Atspi.Value.get_minimum_value(
            self.bar
        )

    @property
    def VerticalScrollPercent(self):
        low, high = (
            Atspi.Value.get_minimum_value(self.bar),
            Atspi.Value.get_maximum_value(self.bar),
        )
        return (
            100 * (Atspi.Value.get_current_value(self.bar) - low) / (high - low)
            if high > low
            else -1
        )

    def SetScrollPercent(self, horizontal, vertical, waitTime=0):
        if horizontal != -1 or not 0 <= vertical <= 100:
            raise ValueError("Only vertical scrolling is supported")
        low, high = (
            Atspi.Value.get_minimum_value(self.bar),
            Atspi.Value.get_maximum_value(self.bar),
        )
        if not Atspi.Value.set_current_value(
            self.bar, low + (high - low) * vertical / 100
        ):
            raise ControlUnavailable("AT-SPI rejected scroll position")
        time.sleep(waitTime)


class Root:
    def __init__(self, auto):
        self.auto = auto

    def GetChildren(self):
        return self.auto.frames()


class Automation:
    file_sort_keys = ("newest", "oldest")
    input_backend = "AT-SPI focus + X11 clipboard and XTEST keyboard input"
    # GTK's location entry accepts one path; import several files through
    # separate, verified picker submissions while keeping the collection open.
    file_import_batch_size = 1
    PatternId = SimpleNamespace(**{name + "Pattern": name for name in PATTERNS})

    def __init__(self):
        self.desktop = Desktop()
        Atspi.init()
        Atspi.set_timeout(1500, 3000)

    @staticmethod
    def pump():
        context = GLib.MainContext.default()
        for _ in range(100):
            if not context.pending():
                break
            context.iteration(False)

    def frames(self, include_hidden=False):
        self.pump()
        windows = self.desktop.windows()
        managed = self.desktop.property(self.desktop.root, "_NET_CLIENT_LIST")
        managed = set(managed) if managed is not None else set()
        root = Atspi.get_desktop(0)
        root.clear_cache()
        result = []
        for index in range(root.get_child_count()):
            app = root.get_child_at_index(index)
            app.clear_cache()
            for child in range(app.get_child_count()):
                frame = app.get_child_at_index(child)
                if frame.get_role_name() not in ("frame", "dialog", "window"):
                    continue
                control = self.wrap(frame, windows=windows)
                if control.native and (
                    include_hidden
                    or control.native.mapped
                    or control.native.id in managed
                ):
                    result.append(control)
        return result

    def wrap(self, obj, *, windows=None, require_window=False):
        control = Control(self, obj)
        if obj.get_role_name() in ("frame", "dialog", "window"):
            rect = control.BoundingRectangle
            bounds = (rect.left, rect.top, rect.width(), rect.height())
            matches = [
                w
                for w in (self.desktop.windows() if windows is None else windows)
                if w.pid == control.ProcessId and w.bounds == bounds
            ]
            if len(matches) > 1:
                matches = [w for w in matches if w.title == control.Name]
            if len(matches) == 1:
                control.native = matches[0]
        if require_window and not control.native:
            raise ControlUnavailable("AT-SPI frame has no unique live X11 window")
        return control

    def GetRootControl(self):
        return Root(self)

    def WalkControl(self, root, maxDepth=0x7FFFFFFF):
        self.pump()
        stack, visited = [(root, 0)], set()
        while stack:
            control, depth = stack.pop()
            identity = control.GetRuntimeId()
            if identity in visited:
                raise ControlUnavailable("Cyclic AT-SPI tree")
            visited.add(identity)
            if len(visited) > 20000:
                raise ControlUnavailable("AT-SPI traversal exceeds node limit")
            if depth:
                yield control, depth
            if depth < maxDepth:
                stack.extend(
                    (child, depth + 1) for child in reversed(control.GetChildren())
                )

    def ControlFromHandle(self, handle):
        matches = [
            c
            for c in self.frames(include_hidden=True)
            if c.NativeWindowHandle == handle
        ]
        if len(matches) != 1:
            raise ControlUnavailable("X11 window has no unique AT-SPI frame")
        return matches[0]

    def GetForegroundControl(self):
        handle = self.desktop.foreground()
        return next((c for c in self.frames() if c.NativeWindowHandle == handle), None)

    def GetFocusedControl(self):
        self.pump()
        frame = self.GetForegroundControl()
        if frame is None:
            return None
        matches = [c for c, _ in self.WalkControl(frame) if c.state("FOCUSED")]
        return matches[-1] if matches else None

    @staticmethod
    def ControlsAreSame(left, right):
        return (
            left is not None
            and right is not None
            and left.GetRuntimeId() == right.GetRuntimeId()
        )

    def SendKeys(self, keys, waitTime=0, **kwargs):
        self.desktop.send_keys(keys)
        time.sleep(waitTime)

    def type_text(self, control, text):
        def checked():
            if not self.ControlsAreSame(self.GetFocusedControl(), control):
                raise FocusLost("QQ editor lost focus during literal text input")

        # Paste Unicode without remapping X11 keycodes. QQ trims pasted edge
        # spaces, so insert those separately; Shift+Enter preserves line breaks.
        checked()
        for index, line in enumerate(text.split("\n")):
            if index:
                checked()
                self.SendKeys("{Shift}{Enter}")
            core = line.strip(" ")
            leading = len(line) - len(line.lstrip(" "))
            trailing = len(line) - len(line.rstrip(" ")) if core else 0
            if leading:
                checked()
                self.desktop.type_text(" " * leading)
            if core:
                self.desktop.set_clipboard(core)
                checked()
                self.SendKeys("{Ctrl}v", waitTime=0.1)
            if trailing:
                checked()
                self.desktop.type_text(" " * trailing)
        checked()

    def copied_rich_text(self):
        return self.desktop.copied_rich_text()

    def copied_files(self):
        return self.desktop.copied_files()

    def GetClipboardText(self):
        return self.desktop.clipboard_text()

    def clipboard_sequence(self):
        return self.desktop.clipboard_sequence()

    def require_unlocked(self):
        self.desktop.ensure_unlocked()

    def is_lock_surface(self, control):
        return control is not None and self.desktop.is_lock_window(control.native)

    def file_picker(self, backend, owner):
        return self.find_file_picker(owner)

    def reveal_profile(self, card):
        from ...ui.backend import wait_until

        wait_until(
            lambda: self.desktop.window(card.NativeWindowHandle, card.ProcessId).mapped,
            timeout=3,
        )
        return card

    def native_cards(self, pid):
        return [
            (c, c.native.mapped)
            for c in self.frames(include_hidden=True)
            if c.ProcessId == pid and c.Name == "资料卡"
        ]

    def find_file_picker(self, owner):
        from Xlib.error import BadWindow

        from .file_picker import FilePicker

        handle, pid = owner.NativeWindowHandle, owner.ProcessId
        self.desktop.window(handle, pid)
        matches = []
        for window in self.desktop.windows():
            if window.pid != pid or not window.mapped:
                continue
            native = self.desktop.display.create_resource_object("window", window.id)
            try:
                parent = native.get_wm_transient_for()
                kind = self.desktop.property(native, "_NET_WM_WINDOW_TYPE")
            except BadWindow:
                # GTK completion popovers can close during window enumeration.
                continue
            if parent is None or parent.id != handle or kind is None:
                continue
            if self.desktop.atom("_NET_WM_WINDOW_TYPE_DIALOG") not in kind:
                continue
            if window.title not in {
                "Open Files",
                "Open File",
                "Save File",
                "Save As",
                "Select Folder",
                "Open",
                "Save",
                "打开文件",
                "打开",
                "保存文件",
                "另存为",
                "选择文件夹",
            }:
                raise ControlUnavailable("Unrecognized native dialog attached to QQ")
            matches.append(FilePicker(self, window, owner))
        if len(matches) > 1:
            raise ControlUnavailable("Multiple native file pickers attached to QQ")
        return matches[0] if matches else None

    def GetCursorPos(self):
        return self.desktop.cursor()

    def SetCursorPos(self, x, y):
        return self.desktop.move_cursor(x, y)

    def ControlFromPoint(self, x, y):
        # X11 stacking order excludes other windows occluding the target.
        stack = self.desktop.property(self.desktop.root, "_NET_CLIENT_LIST_STACKING")
        windows = {w.id: w for w in self.desktop.windows() if w.mapped}
        for handle in reversed(list(stack) if stack is not None else []):
            w = windows.get(int(handle))
            if w is None:
                continue
            left, top, width, height = w.bounds
            if not (left <= x < left + width and top <= y < top + height):
                continue
            try:
                control = self.ControlFromHandle(w.id)
            except ControlUnavailable:
                return None
            documents, dialogs, menus = [], [], []
            for item, depth in self.WalkControl(control, maxDepth=35):
                role = item.obj.get_role_name()
                if (
                    role
                    not in (
                        "document web",
                        "document frame",
                        "dialog",
                        "menu",
                        "popup menu",
                    )
                    or item.IsOffscreen
                ):
                    continue
                rect = item.BoundingRectangle
                if rect.left <= x < rect.right and rect.top <= y < rect.bottom:
                    if role in ("menu", "popup menu"):
                        menus.append(item)
                    elif role == "dialog" and item.state("MODAL"):
                        dialogs.append(item)
                    elif role in ("document web", "document frame"):
                        documents.append((depth, item))
            # Absolute-positioned modal panels can extend beyond their parent's
            # bounds. Chromium's document hit test may otherwise pick a message
            # below the panel. Start inside the unique visible modal first.
            if menus:
                if len(menus) != 1:
                    return None
                control = menus[0]
            elif dialogs:
                if len(dialogs) != 1:
                    return None
                control = dialogs[0]
            elif documents:
                depth = min(depth for depth, _ in documents)
                roots = [item for level, item in documents if level == depth]
                if len(roots) != 1:
                    return None
                control = roots[0]
            for _ in range(64):
                child = Atspi.Component.get_accessible_at_point(
                    control.obj, x, y, Atspi.CoordType.SCREEN
                )
                candidate = Control(self, child) if child is not None else control
                if self.ControlsAreSame(candidate, control):
                    # Chromium's native View stops hit testing at the web-view
                    # bridge. Cross only a unique containing child, then ask
                    # that child's provider to perform the next hit test.
                    containing = []
                    for item in control.GetChildren():
                        if (
                            "Component" not in item.obj.get_interfaces()
                            or item.IsOffscreen
                        ):
                            continue
                        rect = item.BoundingRectangle
                        if rect.left <= x < rect.right and rect.top <= y < rect.bottom:
                            containing.append(item)
                    if len(containing) != 1:
                        return control
                    candidate = containing[0]
                control = candidate
            return None
        return None

    def ControlFromCursor(self):
        return self.ControlFromPoint(*self.GetCursorPos())
