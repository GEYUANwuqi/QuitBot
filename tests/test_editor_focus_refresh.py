from types import SimpleNamespace

from quitbot_backend._engine.uia import UIABackend


def test_focus_editor_uses_recreated_composer_without_replaying_focus_or_input():
    calls, state = [], {"focused": None}
    new = object()

    def focus():
        calls.append("focus")
        state["focused"] = new

    old = SimpleNamespace(SetFocus=focus)
    backend = UIABackend.__new__(UIABackend)
    backend.window = SimpleNamespace(SetActive=lambda **_: calls.append("activate"))
    backend.editor = lambda: new if state["focused"] is not None else old
    backend.auto = SimpleNamespace(
        GetFocusedControl=lambda: state["focused"], ControlsAreSame=lambda a, b: a is b
    )
    assert backend.focus_editor() is new
    assert calls == ["activate", "focus"]


def test_control_focus_resolver_is_explicit_and_checks_fresh_control_identity():
    calls, state = [], {"focused": None}
    current = object()

    def focus():
        calls.append("focus")
        state["focused"] = current

    old = SimpleNamespace(SetFocus=focus)
    b = UIABackend.__new__(UIABackend)
    b.window = SimpleNamespace(SetActive=lambda **_: None)
    b.auto = SimpleNamespace(
        GetFocusedControl=lambda: state["focused"], ControlsAreSame=lambda a, c: a is c
    )
    assert b.focus_control(old, resolve=lambda: current) is current
    assert calls == ["focus"]
