from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.domain.controls import MenuAction
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    MutationOutcomeUnknown,
    TargetMismatch,
)
from quitbot_backend._engine.infrastructure.uia.message_mutations import (
    MessageMutationsUI,
    essence_state,
)
from quitbot_backend._engine.models import Message, Node, Target


def test_essence_state_requires_one_enabled_unambiguous_state():
    assert essence_state([MenuAction("设为精华", True, "")]) is False
    assert essence_state([MenuAction("移除精华", True, "")]) is True
    for rows in (
        [],
        [MenuAction("设为精华", False, "")],
        [MenuAction("设为精华", True, ""), MenuAction("移除精华", True, "")],
    ):
        with pytest.raises(ControlUnavailable):
            essence_state(rows)


@pytest.mark.parametrize(
    "direction,expected,error",
    [
        ("incoming", None, ControlUnavailable),
        ("outgoing", "wrong text", TargetMismatch),
    ],
)
def test_recall_rejects_wrong_owner_and_stale_text_before_ui_submission(
    direction, expected, error
):
    @contextmanager
    def checked(_):
        yield Target("12345", "test")

    ui = SimpleNamespace(checked=checked)
    client = SimpleNamespace(
        control_ui=ui,
        backend=object(),
        get_message=lambda *_: Message("id", "12345", "peer", "text", direction),
    )
    with pytest.raises(error):
        MessageMutationsUI(client).recall("12345", "id", expected_text=expected)


@pytest.mark.parametrize("fail", [False, True])
def test_essence_mutation_is_idempotent_and_never_replayed_after_uncertain_input(fail):
    state, writes = [False], []

    @contextmanager
    def checked(_):
        yield Target("12345", "test")

    def actions(*_):
        return [MenuAction("移除精华" if state[0] else "设为精华", True, "moderation")]

    def invoke(_):
        writes.append(True)
        state[0] = not state[0]
        if fail:
            raise OSError("Provider disconnected after submission")

    ui = SimpleNamespace(
        checked=checked,
        message_menu=lambda *_: object(),
        _actions=actions,
        _menu_item=lambda *_: object(),
        _dismiss_menu=lambda: None,
        by_class=lambda _: None,
        message_actions=actions,
    )
    p = MessageMutationsUI(
        SimpleNamespace(control_ui=ui, backend=SimpleNamespace(invoke=invoke))
    )
    if fail:
        with pytest.raises(MutationOutcomeUnknown):
            p.set_essence("12345", "id", True)
        assert writes == [True] and state[0] is True
    else:
        assert p.set_essence("12345", "id", False).after is False
        assert writes == []
        assert p.set_essence("12345", "id", True).after is True
        assert p.set_essence("12345", "id", True).after is True
        assert len(writes) == 1


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("failure", ["dismiss", "header"])
def test_essence_cleanup_failure_preserves_whether_input_was_submitted(write, failure):
    state, writes = [False], []

    @contextmanager
    def checked(_):
        yield Target("12345", "测试群")
        if failure == "header":
            raise ControlUnavailable("Header changed after operation")

    def actions(*_):
        return [MenuAction("移除精华" if state[0] else "设为精华", True, "")]

    def invoke(_):
        writes.append(True)
        state[0] = True

    def dismiss():
        if failure == "dismiss":
            raise ControlUnavailable("Menu cleanup failed")

    ui = SimpleNamespace(
        checked=checked,
        message_menu=lambda *_: object(),
        _actions=actions,
        _menu_item=lambda *_: object(),
        _dismiss_menu=dismiss,
        by_class=lambda _: None,
        message_actions=actions,
    )
    p = MessageMutationsUI(
        SimpleNamespace(control_ui=ui, backend=SimpleNamespace(invoke=invoke))
    )
    with pytest.raises(MutationOutcomeUnknown if write else ControlUnavailable):
        p.set_essence("12345", "id", write)
    assert state[0] is write and len(writes) == int(write)


def recall_provider(after, *, cleanup_failure=None):
    writes = []
    original = Message("original", "12345", None, "test message", "outgoing")

    @contextmanager
    def checked(_):
        yield Target("12345", "测试群")
        if cleanup_failure == "header":
            raise ControlUnavailable("Header changed after operation")

    def dismiss():
        if cleanup_failure == "dismiss":
            raise ControlUnavailable("Menu cleanup failed")

    b = SimpleNamespace(
        editor=lambda: object(),
        snapshot=lambda _: Node(),
        invoke=lambda _: writes.append(True),
    )
    ui = SimpleNamespace(
        checked=checked,
        message_menu=lambda *_: object(),
        _menu_item=lambda *_: SimpleNamespace(IsEnabled=True),
        _dismiss_menu=dismiss,
        by_class=lambda _: None,
    )
    client = SimpleNamespace(
        control_ui=ui,
        backend=b,
        get_message=lambda *_: original,
        _read=lambda _: after if writes else [original],
    )
    return MessageMutationsUI(client), writes


@pytest.mark.parametrize(
    "row_id,text,success",
    [
        ("original", "08:18你撤回了一条消息 重新编辑", True),
        ("original", "你撤回了一条消息", True),
        ("different", "你撤回了一条消息", False),
        ("original", "peer撤回了一条消息", False),
    ],
)
def test_recall_requires_same_ui_row_and_own_recall_wording(
    monkeypatch, row_id, text, success
):
    p, writes = recall_provider(
        [Message(row_id, "12345", None, text, "system", kind="system")]
    )

    def wait_once(read, **_):
        value = read()
        if not value:
            raise ControlUnavailable("Matching recall row not found")
        return value

    monkeypatch.setattr(
        "quitbot_backend._engine.infrastructure.uia.message_mutations.wait_until", wait_once
    )
    if success:
        assert p.recall("12345", "original").notice_text == text
    else:
        with pytest.raises(MutationOutcomeUnknown):
            p.recall("12345", "original")
    assert writes == [True]


@pytest.mark.parametrize("failure", ["dismiss", "header"])
def test_recall_cleanup_failure_after_successful_write_is_unknown(failure):
    p, writes = recall_provider(
        [
            Message(
                "original", "12345", None, "你撤回了一条消息", "system", kind="system"
            )
        ],
        cleanup_failure=failure,
    )
    with pytest.raises(MutationOutcomeUnknown):
        p.recall("12345", "original")
    assert writes == [True]
