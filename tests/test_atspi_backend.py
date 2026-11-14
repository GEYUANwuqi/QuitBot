"""Native provider boundaries; synthetic controls and no running QQ required."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

pytest.importorskip("gi")
from quitbot_backend._engine.errors import ControlUnavailable, FocusLost
from quitbot_backend._engine.platforms.linux.automation import (
    Atspi,
    Automation,
    Control,
    Rectangle,
)
from quitbot_backend._engine.platforms.linux.x11 import Desktop, Window


class Accessible:
    path = "/fixture/row"
    app = SimpleNamespace(bus_name=":1.99")

    def __init__(self, *, role="section", states=(), actions=(), children=()):
        self.role, self.states, self.names, self.children = (
            role,
            set(states),
            actions,
            children,
        )

    def clear_cache(self):
        pass

    def get_state_set(self):
        return SimpleNamespace(contains=lambda state: state.value_nick in self.states)

    def get_role_name(self):
        return self.role

    def get_role(self):
        name = "push button" if self.role == "button" else self.role
        return getattr(Atspi.Role, name.upper().replace(" ", "_"), Atspi.Role.UNKNOWN)

    def get_attributes(self):
        return {}

    def get_interfaces(self):
        return ["Accessible", "Action", "Component", "Text"]

    def get_action_iface(self):
        return self

    def get_n_actions(self):
        return len(self.names)

    def get_action_name(self, index):
        return self.names[index]

    def get_child_count(self):
        return len(self.children)

    def get_child_at_index(self, index):
        return self.children[index]

    def get_process_id(self):
        return 321

    def get_name(self):
        return "fixture"


@pytest.mark.parametrize("role_name", ["push button", "button"])
def test_discovery_and_binding_accept_button_role_across_atspi_versions(monkeypatch, role_name):
    from quitbot_backend._engine import discovery, platforms
    from quitbot_backend._engine.ui.backend import DesktopBackend

    auto = Automation.__new__(Automation)
    auto.pump = lambda: None
    avatar = Accessible(role=role_name, states=("enabled", "sensitive"), actions=("press",))
    avatar.get_name = lambda: "测试账号的头像"
    avatar.get_attributes = lambda: {"class": "user-avatar"}
    frame = Accessible(role="frame", children=(avatar,))
    frame.path = "/fixture/frame"
    frame.get_name = lambda: "QQ"
    window = Control(auto, frame, Window(42, 321, "QQ", "qq QQ", (0, 0, 800, 600), True))
    auto.frames = lambda: [window]
    monkeypatch.setattr(platforms, "automation", lambda: auto)
    monkeypatch.setattr(discovery, "ui_transaction", nullcontext)

    found = discovery.discover_windows(hwnd=42)
    assert found == [discovery.QQWindow(42, 321, "测试账号")]
    backend = DesktopBackend(found[0].nickname, found[0].hwnd)
    button = backend.find(window, lambda c: "user-avatar" in c.ClassName.split())
    assert button.ControlTypeName == "ButtonControl"
    calls = []
    monkeypatch.setattr(button, "GetTopLevelControl", lambda: window)
    monkeypatch.setattr(Atspi.Action, "do_action", lambda obj, index: calls.append((obj, index)) or True)
    backend.invoke(button)
    assert calls == [(avatar, 0)]


def test_control_type_uses_native_role_without_guessing_from_actions():
    # A provider's display name cannot override the native role, even when the
    # object offers an action with the same name as a button's default action.
    obj = Accessible(role="section", actions=("press",))
    obj.get_role_name = lambda: "push button"
    assert Control(None, obj).ControlTypeName == "GroupControl"


def test_cursor_already_at_destination_does_not_wait_for_a_motion_event():
    desktop = Desktop.__new__(Desktop)
    desktop.cursor = lambda: (20, 80)
    desktop.run = lambda *_: pytest.fail("Same-position --sync would wait forever")
    assert desktop.move_cursor(20, 80) == (20, 80)


def test_activation_waits_for_wm_readback_without_replaying_input(monkeypatch):
    desktop = Desktop.__new__(Desktop)
    states, calls = iter([0, 7, 42]), []
    desktop.window = lambda *args: calls.append(("verify", *args))
    desktop.run = lambda *args: calls.append(args)
    desktop.foreground = lambda: next(states)
    monkeypatch.setattr("quitbot_backend._engine.platforms.linux.x11.time.sleep", lambda _: None)
    desktop.activate(42, 100)
    assert calls == [("verify", 42, 100), ("windowactivate", "--sync", 42)]


def test_process_scan_includes_minimized_managed_main_window_but_not_preloads(monkeypatch):
    from quitbot_backend._engine.platforms.linux import discovery

    windows = [Window(n, 10, "QQ", "qq", (0, 0, 400, 300), False) for n in (42, 43)]
    closed = []
    desktop = SimpleNamespace(
        root=object(), property=lambda *_: [42], windows=lambda: windows,
        close_connection=lambda: closed.append(True),
    )

    class ProcessPath:
        def __init__(self, _):
            pass

        def stat(self):
            return SimpleNamespace(st_uid=discovery.os.getuid())

        def __truediv__(self, _):
            return self

        def resolve(self, **_):
            return SimpleNamespace(name="qq")

    monkeypatch.setattr(discovery, "Path", ProcessPath)
    monkeypatch.setattr("quitbot_backend._engine.platforms.linux.x11.Desktop", lambda: desktop)
    assert discovery.scan_windows() == [{"hwnd": 42, "pid": 10, "process_name": "qq"}]
    assert closed == [True]


def test_toggle_button_uses_pressed_and_advertised_check_actions(monkeypatch):
    obj = Accessible(role="toggle button", states=("pressed",), actions=("uncheck",))
    control = Control(None, obj)
    pattern = control.GetTogglePattern()
    assert pattern.ToggleState == 1
    calls = []
    monkeypatch.setattr(control, "action", lambda names, delay: calls.append(names))
    pattern.Toggle()
    assert "uncheck" in calls[0]
    obj.states.clear()
    assert pattern.ToggleState == 0


def test_menu_selection_and_text_editing_do_not_require_windows_patterns():
    assert Control(
        None, Accessible(role="menu item", actions=("select",))
    ).GetSelectionItemPattern()
    assert Control(
        None, Accessible(role="entry", states=("editable",))
    ).GetValuePattern()
    passive = Control(
        SimpleNamespace(WalkControl=lambda *_args, **_kwargs: []),
        Accessible(actions=("clickAncestor", "showContextMenu")),
    )
    assert passive.GetInvokePattern() is None
    assert passive.GetScrollPattern() is None


def test_expander_uses_advertised_open_action(monkeypatch):
    from quitbot_backend._engine.platforms.linux import automation as module

    obj = Accessible(states=("enabled", "sensitive", "expandable"), actions=("open",))
    control = Control(None, obj)
    calls = []
    monkeypatch.setattr(control, "GetTopLevelControl", lambda: object())
    monkeypatch.setattr(
        module.Atspi.Action,
        "do_action",
        lambda source, index: calls.append(index) or True,
    )
    control.GetExpandCollapsePattern().Expand()
    assert calls == [0]


def test_incomplete_tree_is_not_silently_reported_as_complete():
    with pytest.raises(ControlUnavailable, match="tree changed"):
        Control(None, Accessible(children=(None,))).GetChildren()


def test_window_binding_requires_unique_process_and_geometry(monkeypatch):
    from quitbot_backend._engine.platforms.linux import automation as module

    obj = Accessible(role="frame")
    monkeypatch.setattr(
        module.Atspi.Component,
        "get_extents",
        lambda *_: SimpleNamespace(x=10, y=20, width=400, height=300),
    )
    auto = Automation.__new__(Automation)
    window = Window(11, 321, "fixture", "qq QQ", (10, 20, 400, 300), True)
    assert (
        auto.wrap(obj, windows=[window], require_window=True).NativeWindowHandle == 11
    )
    for windows in (
        [],
        [Window(12, 322, "fixture", "qq QQ", window.bounds, True)],
        [window, window],
    ):
        with pytest.raises(ControlUnavailable, match="unique live X11"):
            auto.wrap(obj, windows=windows, require_window=True)


def test_unicode_paste_stops_before_input_after_focus_changes():
    auto = Automation.__new__(Automation)
    expected = SimpleNamespace(GetRuntimeId=lambda: (":1.99", "/fixture/editor"))
    focused = [expected]
    calls = []
    auto.GetFocusedControl = lambda: focused[0]
    auto.desktop = SimpleNamespace(
        set_clipboard=lambda text: (calls.append(text), focused.__setitem__(0, None))
    )
    auto.SendKeys = lambda keys: calls.append(keys)
    with pytest.raises(FocusLost):
        auto.type_text(expected, "中文\n{Enter}🙂")
    assert calls == ["中文"]


def test_unicode_paste_preserves_edge_spaces_literal_text_and_safe_newlines():
    auto = Automation.__new__(Automation)
    control = SimpleNamespace(GetRuntimeId=lambda: (":1.99", "/fixture/editor"))
    calls = []
    auto.GetFocusedControl = lambda: control
    auto.desktop = SimpleNamespace(
        type_text=lambda text: calls.append(("text", text)),
        set_clipboard=lambda text: calls.append(("clipboard", text)),
    )
    auto.SendKeys = lambda keys, **_: calls.append(("key", keys))
    auto.type_text(control, " 中文🙂 \n{Enter} ")
    assert calls == [
        ("text", " "), ("clipboard", "中文🙂"), ("key", "{Ctrl}v"), ("text", " "),
        ("key", "{Shift}{Enter}"),
        ("clipboard", "{Enter}"), ("key", "{Ctrl}v"), ("text", " "),
    ]


def test_keyboard_chords_are_separate_from_literal_unicode():
    desktop = Desktop.__new__(Desktop)
    calls = []
    desktop.run = lambda *args: calls.append(args)
    desktop.send_keys("{Ctrl}a{Shift}{Enter}{Esc}")
    assert calls == [("key", "--clearmodifiers", "ctrl+a", "shift+Return", "Escape")]
    with pytest.raises(ValueError):
        desktop.send_keys("中文")
    with pytest.raises(ValueError):
        desktop.send_keys("{Ctrl}")
    assert len(calls) == 1


def test_live_message_without_id_cannot_be_silently_skipped():
    obj = Accessible()
    obj.get_attributes = lambda: {"class": "ml-item"}
    with pytest.raises(ControlUnavailable, match="stable DOM id"):
        _ = Control(None, obj).AutomationId


@pytest.mark.parametrize("panels", [0, 1, 2])
def test_modal_hit_testing_precedes_underlying_embedded_messages(monkeypatch, panels):
    from quitbot_backend._engine.platforms.linux import automation as module

    def surface(role, identity):
        return SimpleNamespace(
            obj=SimpleNamespace(get_role=lambda: getattr(Atspi.Role, role.upper().replace(" ", "_"))),
            IsOffscreen=False,
            BoundingRectangle=Rectangle(0, 0, 100, 100),
            state=lambda name: name == "MODAL",
            GetChildren=list,
            GetRuntimeId=lambda: identity,
        )

    auto = Automation.__new__(Automation)
    frame = surface("frame", "frame")
    document = surface("document web", "document")
    dialogs = [surface("dialog", f"dialog-{i}") for i in range(panels)]
    auto.desktop = SimpleNamespace(
        root=object(),
        property=lambda *_: [10],
        windows=lambda: [Window(10, 20, "fixture", "qq QQ", (0, 0, 100, 100), True)],
    )
    auto.ControlFromHandle = lambda _: frame
    auto.WalkControl = lambda *_, **__: [(document, 1), *[(d, 3) for d in dialogs]]
    monkeypatch.setattr(
        module.Atspi.Component, "get_accessible_at_point", lambda *_: None
    )
    assert auto.ControlFromPoint(30, 30) is (
        document if not dialogs else dialogs[0] if panels == 1 else None
    )
