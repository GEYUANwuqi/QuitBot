from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.errors import ControlUnavailable, MutationOutcomeUnknown
from quitbot_backend._engine.models import Node, Target
from quitbot_backend._engine.ui.workflows import chat


@pytest.mark.parametrize("persisted", ["account nickname", "unexpected"])
def test_clear_own_card_accepts_native_nickname_and_reports_unknown_on_mismatch(
    monkeypatch, persisted
):
    state = {"opens": 0}
    field = SimpleNamespace(Value="custom card")
    field.GetValuePattern = lambda: field
    field.SetValue = lambda value, **_: setattr(field, "Value", value)
    calls = []
    b = SimpleNamespace(
        account="account nickname", editor=lambda: object(),
        snapshot=lambda _: Node(), focus_control=lambda _: None,
        ensure_focus=lambda _: None, focus_editor=lambda: None,
        auto=SimpleNamespace(SendKeys=lambda key, **_: calls.append(key)),
    )
    ui = chat.ChatUI(SimpleNamespace(backend=b))
    ui.checked = lambda _: nullcontext(Target("12345", "test"))
    ui._field = lambda *_: field
    ui._settings_panel = lambda: object()
    ui._close_owned_panel = lambda: None

    @contextmanager
    def more():
        state["opens"] += 1
        if state["opens"] == 2:
            field.Value = persisted
        yield object()

    def once(read, predicate=bool, **_):
        value = read()
        if not predicate(value):
            raise ControlUnavailable("Card did not persist")
        return value

    ui.more = more
    monkeypatch.setattr(chat, "wait_until", once)
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)
    if persisted == "unexpected":
        with pytest.raises(MutationOutcomeUnknown):
            ui.set_own_card("12345", "")
    else:
        receipt = ui.set_own_card("12345", "")
        assert receipt.before == "custom card" and receipt.after == persisted
    assert calls == ["{Enter}"]


@pytest.mark.parametrize("text", ["", "unsent draft"])
@pytest.mark.parametrize("settings", [False, True])
def test_panel_cleanup_works_when_editor_focus_is_temporarily_unavailable(
    monkeypatch, text, settings
):
    closed, focused, inputs = [False], [None], []
    button, editor = object(), object()

    def send(key, **_):
        assert focused[0] is button
        inputs.append(key)
        closed[0] = True

    def ensure(expected):
        assert focused[0] is expected

    def invoke(control):
        assert settings and control is button
        inputs.append("toggle-more")
        closed[0] = True

    def focus(control, *, resolve=None):
        focused[0] = resolve() if resolve else control
        return focused[0]

    b = SimpleNamespace(
        auto=SimpleNamespace(SendKeys=send),
        editor=lambda: editor,
        snapshot=lambda _: Node(
            "GroupControl",
            "",
            "ExEditor-qq-msg-editor",
            "",
            (Node("TextControl", text, "", ""),) if text else (),
        ),
        focus_control=focus,
        ensure_focus=ensure,
        invoke=invoke,
    )
    ui = chat.ChatUI(SimpleNamespace(backend=b))
    ui.by_class = lambda _: None if closed[0] else object()
    ui._more_button = lambda: button
    ui._settings_panel = lambda: object() if settings else None
    monkeypatch.setattr(chat, "require_unlocked", lambda _: None)
    ui._close_owned_panel()
    assert inputs == ([] if text else ["toggle-more" if settings else "{Esc}"])
