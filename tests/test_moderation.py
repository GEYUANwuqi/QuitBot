from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.domain.controls import MenuAction
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    MutationOutcomeUnknown,
    TargetMismatch,
)
from quitbot_backend._engine.models import Node, Target
from quitbot_backend._engine.ui.workflows.moderation import (
    ModerationUI,
    ban_state,
)


@pytest.mark.parametrize("role", ["CheckBoxControl", "MenuItemControl"])
def test_custom_mute_handles_both_native_choice_roles(monkeypatch, role):
    selectors = [SimpleNamespace(Name=f"已选中 {v}", ControlTypeName="ButtonControl",
                 ClassName="q-pulldown-menu-button") for v in (2, 0, 0)]
    state = {"dialog": object(), "selector": None}
    dialog, menu = state["dialog"], object()
    for selector in selectors:
        selector.GetExpandCollapsePattern = lambda selector=selector: SimpleNamespace(
            Expand=lambda **_: state.update(selector=selector))
    labels = [SimpleNamespace(Name=n, ControlTypeName="TextControl")
              for n in ("天", "时", "分钟")]
    choices = [SimpleNamespace(Name=str(v), ControlTypeName=role,
               IsEnabled=True, IsOffscreen=False) for v in range(3)]
    button = SimpleNamespace(IsEnabled=True)

    def invoke(control):
        if control is button:
            state["dialog"] = None
        else:
            state["selector"].Name = "已选中 " + control.Name

    for choice in choices:
        choice.GetTogglePattern = lambda choice=choice: SimpleNamespace(
            Toggle=lambda **_: invoke(choice))
    ui = SimpleNamespace(
        by_class=lambda name, *_, **__: (
            state["dialog"] if name == "group-message-forbidden-dialog" else
            Node(type="TextControl", name="peer") if name == "name" else menu),
        _dismiss_menu=lambda: None,
    )
    b = SimpleNamespace(walk=lambda root: selectors + labels if root is dialog else choices,
                        snapshot=lambda x: x, invoke=invoke, find=lambda *_: button)
    submitted = [False]
    ModerationUI(SimpleNamespace(control_ui=ui, backend=b))._custom_ban("peer", 60, submitted)
    assert submitted == [True] and state["dialog"] is None
    assert [s.Name for s in selectors] == ["已选中 0", "已选中 0", "已选中 1"]


@pytest.mark.parametrize("duration", [True, 30, 600.0, 30 * 86400])
def test_invalid_mute_durations_are_rejected_before_ui_access(duration):
    ui = ModerationUI(SimpleNamespace(control_ui=object(), backend=object()))
    with pytest.raises(ValueError):
        ui.set_member_ban("12345", "peer", duration)


def test_missing_or_disabled_permission_is_unknown_not_unmuted():
    for actions in (
        [],
        [MenuAction("举报", True, "privacy")],
        [MenuAction("解除禁言", False, "moderation")],
    ):
        with pytest.raises(ControlUnavailable):
            ban_state(actions)


def provider(*, muted=False, fail_write=False, wrong_readback=False, kind="group"):
    state = [muted]
    calls = []

    @contextmanager
    def checked(_):
        yield Target("12345", "测试群", kind)

    @contextmanager
    def roster(name):
        assert name == "peer"
        yield None

    def actions(_=None):
        return [
            MenuAction("解除禁言" if state[0] else "设置群内禁言", True, "moderation")
        ]

    def invoke(_):
        calls.append("write")
        if fail_write:
            raise OSError("provider disconnected")
        state[0] = not state[0]

    entry = SimpleNamespace(
        IsEnabled=True,
        ClassName="q-context-sub-menu__item",
        GetPattern=lambda _: SimpleNamespace(Expand=lambda **_: None),
    )
    ui = SimpleNamespace(
        checked=checked,
        roster=roster,
        _member_row=lambda _: object(),
        _context=lambda _: object(),
        _actions=actions,
        _menu_item=lambda *_: entry,
        _dismiss_menu=lambda: None,
        by_class=lambda _: object(),
        member_actions=lambda *_: (
            [MenuAction("解除禁言", True, "moderation")]
            if wrong_readback
            else actions()
        ),
    )
    b = SimpleNamespace(
        invoke=invoke,
        auto=SimpleNamespace(PatternId=SimpleNamespace(ExpandCollapsePattern=1)),
    )
    return ModerationUI(SimpleNamespace(control_ui=ui, backend=b)), calls


def test_unmute_is_idempotent_and_never_invents_a_member_qq_number():
    ui, calls = provider()
    receipt = ui.set_member_ban("12345", "peer", 0)
    assert (
        not receipt.before
        and not receipt.after
        and receipt.user_id is None
        and not calls
    )
    ui, calls = provider(muted=True)
    receipt = ui.set_member_ban("12345", "peer", 0)
    assert receipt.before and not receipt.after and calls == ["write"]


def test_native_preset_sets_mute_and_reports_requested_duration_separately():
    ui, calls = provider()
    receipt = ui.set_member_ban("12345", "peer", 600)
    assert (
        receipt.after and receipt.requested_duration == 600 and receipt.user_id is None
    )
    assert calls == ["write"]


def test_existing_mute_duration_is_not_guessed_or_implicitly_replaced():
    ui, calls = provider(muted=True)
    with pytest.raises(ControlUnavailable, match="current duration is unknown"):
        ui.set_member_ban("12345", "peer", 600)
    assert not calls


@pytest.mark.parametrize("failure", ["write", "readback"])
def test_failures_after_submission_report_unknown_and_never_repeat_mutation(failure):
    ui, calls = provider(
        muted=True, fail_write=failure == "write", wrong_readback=failure == "readback"
    )
    with pytest.raises(MutationOutcomeUnknown):
        ui.set_member_ban("12345", "peer", 0)
    assert calls == ["write"]


def test_private_conversation_rejects_member_moderation_before_writing():
    ui, calls = provider(kind="private")
    with pytest.raises(ValueError):
        ui.set_member_ban("12345", "peer", 0)
    assert not calls


def test_removal_refuses_conflicting_numeric_identity_before_menu_or_confirmation():
    calls = []

    @contextmanager
    def checked(_):
        yield Target("12345", "测试群")

    ui = SimpleNamespace(
        checked=checked,
        by_class=lambda _: None,
        member_profile=lambda *_: SimpleNamespace(user_id="99999", role="member"),
    )
    b = SimpleNamespace(invoke=lambda _: calls.append("invoke"))
    operation = ModerationUI(SimpleNamespace(control_ui=ui, backend=b))
    with pytest.raises(TargetMismatch):
        operation.kick_member("12345", "peer", expected_user_id="88888")
    assert calls == []


@pytest.mark.parametrize("number", [True, None, "1"])
def test_removal_rejects_unverifiable_identity_before_accessing_ui(number):
    operation = ModerationUI(SimpleNamespace(control_ui=object(), backend=object()))
    with pytest.raises(ValueError):
        operation.kick_member("12345", "peer", expected_user_id=number)
