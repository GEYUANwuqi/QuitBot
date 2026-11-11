from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.domain.controls import ChatSettings
from quitbot_backend._engine.errors import ControlUnavailable
from quitbot_backend._engine.infrastructure.uia.chat import ChatUI
from quitbot_backend._engine.models import Target


def blocking_ui(kind="private", confirmation=False):
    state = [False]
    calls = []

    def toggle(**_):
        state[0] = not state[0]
        calls.append("toggle")

    pattern = SimpleNamespace(Toggle=toggle)
    switch = SimpleNamespace(GetPattern=lambda _: pattern)
    ui = ChatUI(
        SimpleNamespace(
            backend=SimpleNamespace(
                auto=SimpleNamespace(PatternId=SimpleNamespace(TogglePattern=1))
            )
        )
    )

    @contextmanager
    def checked(_):
        yield Target("12345", "peer", kind)

    @contextmanager
    def more():
        yield object()

    ui.checked = checked
    ui.more = more
    ui.by_class = lambda _: object() if confirmation and state[0] else None
    ui._switch = lambda *_: switch
    ui._switch_value = lambda _: state[0]
    ui.settings = lambda _: ChatSettings("12345", False, False, state[0])
    return ui, calls


def test_block_setting_is_idempotent_and_confirmed_by_reread():
    ui, calls = blocking_ui()
    assert ui.set_blocked("12345", False).before is False and not calls
    assert ui.set_blocked("12345", True).after is True and calls == ["toggle"]
    assert ui.set_blocked("12345", True).before is True and calls == ["toggle"]
    assert ui.set_blocked("12345", False).after is False and calls == [
        "toggle",
        "toggle",
    ]


def test_group_or_nonboolean_block_requests_do_not_toggle():
    ui, calls = blocking_ui(kind="group")
    with pytest.raises(ValueError):
        ui.set_blocked("12345", True)
    with pytest.raises(TypeError):
        ui.set_blocked("12345", 1)
    assert not calls


def test_unknown_block_confirmation_is_not_automatically_submitted():
    ui, calls = blocking_ui(confirmation=True)
    with pytest.raises(ControlUnavailable, match="confirmation"):
        ui.set_blocked("12345", True)
    assert calls == ["toggle"]
