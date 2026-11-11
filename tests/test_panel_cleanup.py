from types import SimpleNamespace

import pytest

from quitbot_backend._engine.infrastructure.uia import chat
from quitbot_backend._engine.models import Node


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
