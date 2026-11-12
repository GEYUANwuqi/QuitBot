"""Native provider boundaries; synthetic controls and no running QQ required."""

from types import SimpleNamespace

import pytest

pytest.importorskip("gi")
from quitbot_backend._engine.errors import ControlUnavailable, FocusLost
from quitbot_backend._engine.infrastructure.atspi.automation import Automation, Control
from quitbot_backend._engine.infrastructure.atspi.x11 import Desktop, Window


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


def test_incomplete_tree_is_not_silently_reported_as_complete():
    with pytest.raises(ControlUnavailable, match="tree changed"):
        Control(None, Accessible(children=(None,))).GetChildren()


def test_window_binding_requires_unique_process_and_geometry(monkeypatch):
    from quitbot_backend._engine.infrastructure.atspi import automation as module

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


def test_unicode_paste_rechecks_focus_after_clipboard_write():
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
    assert calls == ["中文\n{Enter}🙂"]


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
