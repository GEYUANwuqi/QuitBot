from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.errors import ControlUnavailable, MutationOutcomeUnknown
from quitbot_backend._engine.models import Target
from quitbot_backend._engine.ui.workflows.preferences import (
    PreferencesUI,
    selected_label,
)


@pytest.mark.parametrize(
    "method,value",
    [
        ("set_muted", 1),
        ("set_speak_limit", True),
        ("set_speak_limit", 7),
        ("set_message_mode", "unknown"),
    ],
)
def test_invalid_preferences_never_touch_ui(method, value):
    p = PreferencesUI(SimpleNamespace(control_ui=object(), backend=object()))
    with pytest.raises((TypeError, ValueError)):
        getattr(p, method)("12345", value)


def test_selected_label_never_guesses_unselected_text():
    assert selected_label(" 已选中 每分钟5条") == "每分钟5条"
    assert selected_label("每分钟5条") is None


def provider(*, fail=False, disabled_state=False):
    state, writes = [False], []

    @contextmanager
    def checked(_):
        yield Target("12345", "test", "private")

    @contextmanager
    def more():
        yield object()

    def toggle(**_):
        writes.append(True)
        state[0] = not state[0]
        if fail:
            raise OSError("write result lost")

    control = SimpleNamespace(
        IsEnabled=True, GetTogglePattern=lambda: SimpleNamespace(Toggle=toggle)
    )
    ui = SimpleNamespace(
        checked=checked,
        more=more,
        _switch=lambda *_: control,
        _switch_value=lambda _: None if disabled_state else state[0],
        by_class=lambda _: None,
    )
    return (
        PreferencesUI(SimpleNamespace(control_ui=ui, backend=object())),
        state,
        writes,
    )


def test_boolean_is_idempotent_and_rereads_state():
    p, state, writes = provider()
    assert p.set_muted("12345", False).after is False
    assert writes == []
    receipt = p.set_muted("12345", True)
    assert receipt.before is False and receipt.after is True and state == [True]
    assert len(writes) == 1


def test_unknown_state_is_never_toggled():
    p, _, writes = provider(disabled_state=True)
    with pytest.raises(ControlUnavailable):
        p.set_muted("12345", True)
    assert writes == []


def test_mutation_failure_is_unknown_and_never_retried():
    p, state, writes = provider(fail=True)
    with pytest.raises(MutationOutcomeUnknown):
        p.set_muted("12345", True)
    assert state == [True] and len(writes) == 1


@pytest.mark.parametrize("changed", [True, False])
@pytest.mark.parametrize("lost_header", [True, False])
def test_group_mode_rereads_existing_window_then_rebinds_only_if_needed(
    changed, lost_header
):
    state, writes, routes = ["silent"], [], []

    @contextmanager
    def checked(_):
        yield Target("12345", "测试群")
        if lost_header:
            raise ControlUnavailable(
                "Header disappeared after group moved into assistant"
            )

    @contextmanager
    def more():
        yield object()

    client = SimpleNamespace(
        control_ui=SimpleNamespace(checked=checked, more=more),
        backend=object(),
        target=lambda _: Target("12345", "测试群"),
        open_chat=lambda target, route: routes.append((target.id, route)),
    )
    p = PreferencesUI(client)

    def read(_):
        if writes and lost_header and not routes:
            raise ControlUnavailable("Need to rebind the replaced window")
        return SimpleNamespace(message_mode=state[0])

    p.group_preferences = read
    p.set_muted = lambda *_: SimpleNamespace(before=True, after=True)
    p._dropdown = lambda *_: object()

    def select(control, label, *, submitted, collapse_after_submit):
        assert collapse_after_submit is False
        submitted[0] = True
        writes.append(label)
        if changed:
            state[0] = "assistant"

    p._select = select
    if changed:
        assert p.set_message_mode("12345", "assistant").after == "assistant"
    else:
        with pytest.raises(MutationOutcomeUnknown):
            p.set_message_mode("12345", "assistant")
    assert writes == ["收进群助手且不提醒"]
    assert routes == ([("12345", "contacts")] if lost_header else [])


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("failure", ["cleanup", "reread", "header"])
def test_boolean_preserves_unknown_outcome_after_cleanup_or_read_failure(
    write, failure
):
    p, state, writes = provider()
    opens = []

    @contextmanager
    def more():
        opens.append(True)
        if failure == "reread" and len(opens) == 2:
            raise ControlUnavailable("Fresh settings panel unavailable")
        yield object()
        if failure == "cleanup":
            raise ControlUnavailable("Panel cleanup failed")

    @contextmanager
    def checked(_):
        yield Target("12345", "test", "private")
        if failure == "header":
            raise ControlUnavailable("Header replaced after action")

    p.ui.more, p.ui.checked = more, checked
    with pytest.raises(MutationOutcomeUnknown if write else ControlUnavailable):
        p.set_muted("12345", write)
    assert state[0] is write and len(writes) == int(write)


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("failure", ["cleanup", "reread"])
def test_speak_limit_preserves_unknown_outcome_after_write(write, failure):
    state, writes, reads = [0], [], []

    @contextmanager
    def checked(_):
        yield Target("12345", "测试群")
        if failure == "cleanup":
            raise ControlUnavailable("Header cleanup failed")

    @contextmanager
    def more():
        yield object()

    p = PreferencesUI(
        SimpleNamespace(
            control_ui=SimpleNamespace(checked=checked, more=more), backend=object()
        )
    )

    def read(_):
        reads.append(True)
        if len(reads) > 1:
            raise ControlUnavailable("Settings read failed")
        return SimpleNamespace(speak_limit=state[0])

    def select(control, label, *, submitted):
        submitted[0] = True
        writes.append(label)
        state[0] = 5

    p.group_preferences, p._dropdown, p._select = read, lambda *_: object(), select
    with pytest.raises(MutationOutcomeUnknown if write else ControlUnavailable):
        p.set_speak_limit("12345", 5 if write else 0)
    assert len(writes) == int(write)


def test_idempotent_boolean_changed_by_external_actor_does_not_claim_own_mutation():
    p, _, writes = provider()
    readings = iter((False, True))
    p.ui._switch_value = lambda _: next(readings)
    with pytest.raises(ControlUnavailable):
        p.set_muted("12345", False)
    assert writes == []


def test_idempotent_limit_changed_externally_does_not_claim_own_mutation():
    p = PreferencesUI(SimpleNamespace(control_ui=object(), backend=object()))
    readings = iter((0, 5))
    p.group_preferences = lambda _: SimpleNamespace(speak_limit=next(readings))
    with pytest.raises(ControlUnavailable):
        p.set_speak_limit("12345", 0)
