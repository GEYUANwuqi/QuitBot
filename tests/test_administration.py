from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.domain.controls import MemberProfile, MenuAction
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    MutationOutcomeUnknown,
    TargetMismatch,
)
from quitbot_backend._engine.models import GroupMemberInfo, Node, Target
from quitbot_backend._engine.ui.workflows.administration import (
    AdministrationUI,
    admin_state,
)


@pytest.fixture(autouse=True)
def native_guards(monkeypatch):
    monkeypatch.setattr(
        "quitbot_backend._engine.ui.workflows.administration.require_unlocked", lambda _: None
    )

    def wait_once(read, predicate=bool, **_):
        result = read()
        if not predicate(result):
            raise ControlUnavailable("Postcondition did not match")
        return result

    monkeypatch.setattr(
        "quitbot_backend._engine.ui.workflows.administration.wait_until", wait_once
    )


@pytest.mark.parametrize(
    "actions",
    [
        [],
        [MenuAction("设为管理员", False, "moderation")],
        [MenuAction("设为管理员", True, "moderation")] * 2,
        [
            MenuAction(label, True, "moderation")
            for label in ("设为管理员", "取消管理员")
        ],
    ],
)
def test_admin_state_requires_one_enabled_role_action(actions):
    with pytest.raises(ControlUnavailable):
        admin_state(actions)


def provider(
    *,
    initial=False,
    actual_id="34567",
    profile_role=None,
    kind="group",
    invalid_dialog=None,
    failure=None,
    mismatch_role=False,
    mismatch_menu=False,
):
    state = {"admin": initial, "dialog": None}
    calls = []
    target = Target("12345", "测试群", kind)
    button = lambda name: SimpleNamespace(
        ControlTypeName="ButtonControl", Name=name, IsEnabled=True, IsOffscreen=False
    )
    confirm = button("确定")
    buttons = [button("关闭"), confirm, button("取消")]
    dialog = object()
    leaf = SimpleNamespace(IsEnabled=True)

    @contextmanager
    def checked(_):
        yield target
        if failure == "checked_cleanup":
            raise ControlUnavailable("Header read failed")

    @contextmanager
    def roster(_):
        yield None
        if failure == "roster_cleanup":
            raise ControlUnavailable("Roster cleanup failed")

    def actions():
        value = state["admin"]
        if mismatch_menu and "confirm" in calls:
            value = not value
        return [MenuAction("取消管理员" if value else "设为管理员", True, "moderation")]

    def by_class(name, root=None, **_):
        if name == "q-dialog-main":
            return state["dialog"]
        desired = not initial
        if name == "q-dialog-title":
            value = "设为管理员" if desired else "取消管理员"
            if invalid_dialog == "title":
                value = "移出本群"
        elif name == "q-dialog-content":
            value = (
                "确定要设置 peer 为管理员吗？"
                if desired
                else "确定要取消 peer 管理员权限吗？"
            )
            if invalid_dialog == "member":
                value = value.replace("peer", "different member")
        else:
            raise AssertionError(name)
        return Node("TextControl", value)

    def invoke(control):
        if control is leaf:
            calls.append("open")
            state["dialog"] = dialog
        elif control is confirm:
            calls.append("confirm")
            state["admin"] = not initial
            state["dialog"] = None
            if failure == "confirmation":
                raise OSError("Disconnected after submit")
        else:
            raise AssertionError("Unexpected invocation")

    def dismiss():
        calls.append("dismiss")
        if failure == "dismiss":
            raise ControlUnavailable("Menu cleanup failed")

    def members(*_, **__):
        if failure == "readback":
            raise ControlUnavailable("Roster read failed")
        value = state["admin"] != mismatch_role
        return [GroupMemberInfo("peer", "admin" if value else "member")]

    def profile(*_):
        calls.append("profile")
        return MemberProfile(
            target.id,
            "peer",
            actual_id,
            profile_role or ("admin" if initial else "member"),
            (),
        )

    controls = list(buttons)
    if invalid_dialog == "extra_button":
        controls.append(button("同时删除"))
    if invalid_dialog == "option":
        controls.append(SimpleNamespace(ControlTypeName="CheckBoxControl"))
    ui = SimpleNamespace(
        checked=checked,
        roster=roster,
        by_class=by_class,
        member_profile=profile,
        _member_row=lambda _: object(),
        _context=lambda _: object(),
        _actions=lambda _: actions(),
        _menu_item=lambda *_: leaf,
        _dismiss_menu=dismiss,
        members=members,
        member_actions=lambda *_: actions(),
    )
    b = SimpleNamespace(
        auto=object(),
        header=lambda: SimpleNamespace(Name=target.name),
        walk=lambda _: controls,
        snapshot=lambda control: control,
        invoke=invoke,
    )
    return AdministrationUI(SimpleNamespace(control_ui=ui, backend=b)), calls, state


@pytest.mark.parametrize("initial", [True, False])
def test_set_and_remove_admin_require_fresh_identity_and_both_readbacks(initial):
    ui, calls, state = provider(initial=initial)
    receipt = ui.set_member_admin(
        "12345", "peer", not initial, expected_user_id="34567"
    )
    assert calls == ["profile", "open", "confirm", "dismiss"]
    assert receipt.before is initial and receipt.after is not initial
    assert receipt.user_id == "34567" and receipt.identity_source == "fresh_uia_profile"
    assert state["dialog"] is None


@pytest.mark.parametrize("initial", [True, False])
def test_idempotent_admin_does_not_open_or_submit_confirmation(initial):
    ui, calls, _ = provider(initial=initial)
    receipt = ui.set_member_admin("12345", "peer", initial, expected_user_id="34567")
    assert receipt.before is receipt.after is initial
    assert calls == ["profile", "dismiss"]


def test_invalid_boolean_is_rejected_before_ui():
    ui = AdministrationUI(SimpleNamespace(control_ui=object(), backend=object()))
    with pytest.raises(TypeError):
        ui.set_member_admin("12345", "peer", 1, expected_user_id="34567")


@pytest.mark.parametrize("number", [True, None, "1", "３４５６７"])
def test_invalid_identity_is_rejected_before_ui(number):
    ui = AdministrationUI(SimpleNamespace(control_ui=object(), backend=object()))
    with pytest.raises(ValueError):
        ui.set_member_admin("12345", "peer", True, expected_user_id=number)


@pytest.mark.parametrize(
    "options,error",
    [
        ({"actual_id": "45678"}, TargetMismatch),
        ({"profile_role": "owner"}, ControlUnavailable),
        ({"profile_role": "unknown"}, ControlUnavailable),
        ({"profile_role": "admin"}, TargetMismatch),
        ({"kind": "private"}, ValueError),
    ],
)
def test_bad_identity_role_or_scope_never_opens_action(options, error):
    ui, calls, _ = provider(**options)
    with pytest.raises(error):
        ui.set_member_admin("12345", "peer", True, expected_user_id="34567")
    assert "open" not in calls and "confirm" not in calls


@pytest.mark.parametrize("shape", ["title", "member", "extra_button", "option"])
def test_unknown_confirmation_is_retained_without_submission(shape):
    ui, calls, state = provider(invalid_dialog=shape)
    with pytest.raises(ControlUnavailable):
        ui.set_member_admin("12345", "peer", True, expected_user_id="34567")
    assert calls == ["profile", "open"]
    assert state["dialog"] is not None and state["admin"] is False


@pytest.mark.parametrize(
    "failure",
    ["confirmation", "readback", "dismiss", "roster_cleanup", "checked_cleanup"],
)
def test_postsubmission_failures_remain_unknown_and_never_replay(failure):
    ui, calls, _ = provider(failure=failure)
    with pytest.raises(MutationOutcomeUnknown):
        ui.set_member_admin("12345", "peer", True, expected_user_id="34567")
    assert calls.count("open") == calls.count("confirm") == 1


@pytest.mark.parametrize("options", [{"mismatch_role": True}, {"mismatch_menu": True}])
def test_role_and_menu_must_both_confirm_after_submission(options):
    ui, calls, _ = provider(**options)
    with pytest.raises(MutationOutcomeUnknown):
        ui.set_member_admin("12345", "peer", True, expected_user_id="34567")
    assert calls.count("confirm") == 1
