from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.domain.controls import MemberProfile
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    MutationOutcomeUnknown,
    TargetMismatch,
)
from quitbot_backend._engine.models import GroupMemberInfo, Node, Target
from quitbot_backend._engine.ui.workflows.member_interactions import (
    MemberInteractionsUI,
)


@pytest.fixture(autouse=True)
def native_guards(monkeypatch):
    monkeypatch.setattr(
        "quitbot_backend._engine.ui.workflows.member_interactions.require_unlocked",
        lambda _: None,
    )

    def wait_once(read, predicate=bool, **_):
        result = read()
        if not predicate(result):
            raise ControlUnavailable("Postcondition did not match")
        return result

    monkeypatch.setattr(
        "quitbot_backend._engine.ui.workflows.member_interactions.wait_until", wait_once
    )


def provider(
    *, actual_id="34567", kind="group", shape=None, failure=None, collision=False
):
    calls = []
    state = {"card": "peer", "dialog": None}
    target = Target("12345", "测试群", kind)
    controls = []
    field = SimpleNamespace(
        ControlTypeName="EditControl",
        ClassName="el-textarea__inner",
        IsEnabled=True,
        IsOffscreen=False,
        Value="peer",
    )
    field.GetValuePattern = lambda: field

    def set_value(value, **_):
        field.Value = value
        calls.append(("edit", value))

    field.SetValue = set_value
    buttons = {
        name: SimpleNamespace(
            Name=name,
            ControlTypeName="ButtonControl",
            IsEnabled=True,
            IsOffscreen=False,
        )
        for name in ("关闭", "确定", "取消")
    }
    controls.extend([field, *buttons.values()])
    if shape == "option":
        controls.append(SimpleNamespace(ControlTypeName="CheckBoxControl"))
    if shape == "extra_edit":
        controls.append(field)
    if shape == "extra_button":
        controls.append(SimpleNamespace(Name="删除", ControlTypeName="ButtonControl"))
    dialog = SimpleNamespace(
        ClassName="q-dialog-main"
        if shape == "class"
        else "group-edit-dialog q-dialog-main"
    )
    leaf = SimpleNamespace(IsEnabled=shape != "disabled", IsOffscreen=False)

    @contextmanager
    def checked(_):
        yield target
        if failure == "checked_cleanup":
            raise ControlUnavailable("Checked cleanup failed")

    @contextmanager
    def roster(name):
        assert name == state["card"]
        yield None
        if failure == "roster_cleanup" and "confirm" in calls:
            raise ControlUnavailable("Roster cleanup failed")

    def profile(_, name):
        assert name == state["card"]
        calls.append(("profile", name))
        number = (
            "45678"
            if failure == "identity_readback" and "confirm" in calls
            else actual_id
        )
        ui.identities[(target.id, name)] = number
        return MemberProfile(target.id, name, number, "member", (), "original nickname")

    def members(*_, query):
        if failure == "readback" and "confirm" in calls:
            raise ControlUnavailable("Roster read failed")
        names = [state["card"]] + (["new"] if collision else [])
        return [GroupMemberInfo(name, "member") for name in names if query in name]

    def by_class(name, *_args, **_kwargs):
        if name == "q-dialog-main":
            return state["dialog"]
        if name == "q-dialog-title":
            return Node("TextControl", "移出本群" if shape == "title" else "修改群昵称")
        raise AssertionError(name)

    def invoke(control):
        if control is leaf:
            calls.append("open")
            field.Value = (
                "other"
                if failure == "persisted" and "confirm" in calls
                else state["card"]
            )
            state["dialog"] = dialog
        elif control is buttons["确定"]:
            calls.append("confirm")
            state["card"] = field.Value
            state["dialog"] = None
            if failure == "confirmation":
                raise OSError("Disconnected after submission")
        elif control is buttons["取消"]:
            calls.append("cancel")
            state["dialog"] = None
        else:
            raise AssertionError("Unexpected invocation")

    ui = SimpleNamespace(
        checked=checked,
        roster=roster,
        by_class=by_class,
        member_profile=profile,
        members=members,
        identities={},
        _context=lambda _: object(),
        _member_row=lambda _: object(),
        _menu_item=lambda *_: leaf,
        _dismiss_menu=lambda: calls.append("dismiss"),
    )
    backend = SimpleNamespace(
        auto=object(),
        header=lambda: SimpleNamespace(Name=target.name),
        snapshot=lambda node: node,
        walk=lambda _: controls,
        invoke=invoke,
        focus_control=lambda _: None,
    )
    return (
        MemberInteractionsUI(SimpleNamespace(control_ui=ui, backend=backend)),
        state,
        calls,
    )


def test_member_card_requires_numeric_profile_and_persisted_editor_readback():
    ui, state, calls = provider()
    receipt = ui.set_member_card("12345", "peer", "new", expected_user_id="34567")
    assert receipt.before == "peer" and receipt.after == "new"
    assert (receipt.before_name, receipt.after_name, receipt.nickname) == (
        "peer",
        "new",
        "original nickname",
    )
    assert calls == [
        ("profile", "peer"),
        "open",
        ("edit", "new"),
        "confirm",
        "dismiss",
        ("profile", "new"),
        "open",
        "cancel",
        "dismiss",
    ]
    assert state["dialog"] is None
    assert ui.ui.identities == {("12345", "new"): "34567"}


def test_same_card_cancels_without_submission():
    ui, state, calls = provider()
    receipt = ui.set_member_card("12345", "peer", "peer", expected_user_id="34567")
    assert receipt.before == receipt.after == "peer"
    assert calls == [("profile", "peer"), "open", "cancel", "dismiss"]
    assert state["dialog"] is None


@pytest.mark.parametrize("value", ["", None, "bad\ncard", "x" * 61])
def test_invalid_or_unverified_clear_card_never_reaches_ui(value):
    ui, _, calls = provider()
    with pytest.raises(ValueError):
        ui.set_member_card("12345", "peer", value, expected_user_id="34567")
    assert calls == []


@pytest.mark.parametrize("number", [True, None, "１２３４５", "1"])
def test_invalid_numeric_member_identity_never_reaches_ui(number):
    ui, _, calls = provider()
    with pytest.raises(ValueError):
        ui.set_member_card("12345", "peer", "new", expected_user_id=number)
    assert calls == []


@pytest.mark.parametrize(
    "shape", ["class", "title", "option", "extra_edit", "extra_button"]
)
def test_unknown_card_dialog_is_retained_without_submission(shape):
    ui, state, calls = provider(shape=shape)
    with pytest.raises(ControlUnavailable):
        ui.set_member_card("12345", "peer", "new", expected_user_id="34567")
    assert state["dialog"] is not None
    assert "confirm" not in calls and "cancel" not in calls


@pytest.mark.parametrize(
    "options,error",
    [
        ({"actual_id": "45678"}, TargetMismatch),
        ({"kind": "private"}, ValueError),
        ({"collision": True}, TargetMismatch),
        ({"shape": "disabled"}, ControlUnavailable),
    ],
)
def test_member_identity_scope_collision_and_permission_reject_before_submit(
    options, error
):
    ui, _, calls = provider(**options)
    with pytest.raises(error):
        ui.set_member_card("12345", "peer", "new", expected_user_id="34567")
    assert "confirm" not in calls


@pytest.mark.parametrize(
    "failure",
    [
        "confirmation",
        "readback",
        "identity_readback",
        "persisted",
        "roster_cleanup",
        "checked_cleanup",
    ],
)
def test_postsubmit_failure_is_unknown_outcome_and_never_retried(failure):
    ui, _, calls = provider(failure=failure)
    with pytest.raises(MutationOutcomeUnknown):
        ui.set_member_card("12345", "peer", "new", expected_user_id="34567")
    assert calls.count("confirm") == 1
    assert ("12345", "peer") not in ui.ui.identities
