from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.adapters.onebot.api import OneBotAdapter
from quitbot_backend._engine.domain.activities import group_notice
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    MutationOutcomeUnknown,
    TargetMismatch,
)
from quitbot_backend._engine.infrastructure.uia.member_interactions import (
    MemberInteractionsUI,
)
from quitbot_backend._engine.models import Message, Target


@pytest.mark.parametrize("text,sender,recipient", [
    ("22:32peer戳了戳你", 23456, 12345),
    ("22:32你戳了戳peer", 12345, 23456),
])
def test_private_poke_distinguishes_peer_from_sender_and_rejects_unproven_rows(
    text, sender, recipient
):
    target = Target("23456", "peer", "private")
    message = Message("row", target.id, None, text, "system", "22:32", "system")
    adapter = OneBotAdapter(SimpleNamespace(targets=[target]), 12345)
    try:
        event = adapter.notice_event(target, message)
        assert (event["user_id"], event["sender_id"], event["target_id"]) == (
            23456, sender, recipient)
        assert "group_id" not in event
        for invalid in (
            replace(message, direction="incoming", kind="text"),
            replace(message, conversation_id="99999"),
            replace(message, text=text + "自定义后缀"),
            replace(message, text=text.replace("peer", "unknown")),
        ):
            assert adapter.notice_event(target, invalid) is None
    finally:
        adapter.index.close()


def test_private_poke_rechecks_identity_after_revealing_older_named_avatar(monkeypatch):
    target = Target("23456", "peer", "private")
    calls = []
    older = SimpleNamespace(Name="peer", IsEnabled=True, IsOffscreen=True)
    latest = SimpleNamespace(Name="", IsEnabled=True, IsOffscreen=False)

    def reveal(**_):
        calls.append("scroll")
        older.Name, older.IsOffscreen = "other", False

    older.GetPattern = lambda _: SimpleNamespace(ScrollIntoView=reveal)
    rows = [SimpleNamespace(AutomationId="older", ClassName="ml-item", avatar=older),
            SimpleNamespace(AutomationId="latest", ClassName="ml-item", avatar=latest)]
    ui = SimpleNamespace(
        checked=lambda _: nullcontext(target),
        by_class=lambda name, root=None, **_: root.avatar if root is not None else None,
    )
    b = SimpleNamespace(
        window=object(), walk=lambda _: rows,
        find=lambda _, predicate: next(row for row in rows if predicate(row)),
        auto=SimpleNamespace(PatternId=SimpleNamespace(ScrollItemPattern=1)),
    )
    client = SimpleNamespace(control_ui=ui, backend=b, _read=lambda _: [
        Message(row.AutomationId, target.id, "peer", "text", "incoming") for row in rows])
    with pytest.raises(TargetMismatch, match="identity/visibility"):
        MemberInteractionsUI(client).poke_friend(target)
    assert calls == ["scroll"]


@pytest.mark.parametrize(
    "body,self_id,mapping,actor,target",
    [
        ("你戳了戳peer", 12345, {"peer": 23456}, 12345, 23456),
        ("owner戳了戳你", 23456, {"owner": 12345}, 12345, 23456),
    ],
)
def test_observed_poke_wording_resolves_both_bound_actors(
    body, self_id, mapping, actor, target
):
    message = Message("row", "34567", None, "20:42" + body, "system", "20:42", "system")
    notice = group_notice(message, self_id, mapping)
    assert (notice["notice_type"], notice["sub_type"]) == ("notify", "poke")
    assert (notice["user_id"], notice["target_id"]) == (actor, target)
    assert group_notice(message, self_id, {}) is None


def test_normal_text_or_unknown_custom_poke_does_not_become_a_notice():
    for kind, direction, text in [
        ("text", "incoming", "你戳了戳peer"),
        ("system", "system", "你戳了戳peer并说了什么"),
    ]:
        assert (
            group_notice(
                Message("row", "34567", None, text, direction, kind=kind),
                12345,
                {"peer": 23456},
            )
            is None
        )


def poke_provider(monkeypatch, *, number="23456", received=True, existing=False):
    module = "quitbot_backend._engine.infrastructure.uia.member_interactions"
    monkeypatch.setattr(module + ".require_unlocked", lambda _: None)

    def once(read, predicate=bool, **_):
        value = read()
        if not predicate(value):
            raise ControlUnavailable("Missing new receipt")
        return value

    monkeypatch.setattr(module + ".wait_until", once)
    target = Target("34567", "测试群")
    row = Message("poke-row", target.id, None, "你戳了戳peer", "system", kind="system")
    rows = [row] if existing else []
    calls = []
    leaf = SimpleNamespace(IsEnabled=True, IsOffscreen=False)
    ui = SimpleNamespace(
        checked=lambda _: nullcontext(target),
        by_class=lambda _: None,
        member_profile=lambda *_: SimpleNamespace(user_id=number),
        roster=lambda _: nullcontext(),
        _member_row=lambda _: object(),
        _context=lambda _: object(),
        _menu_item=lambda *_: leaf,
        _dismiss_menu=lambda: None,
    )

    def invoke(_):
        calls.append("submit")
        if received and not existing:
            rows.append(row)

    b = SimpleNamespace(
        auto=None, header=lambda: SimpleNamespace(Name="测试群"), invoke=invoke
    )
    client = SimpleNamespace(control_ui=ui, backend=b, _read=lambda _: list(rows))
    return MemberInteractionsUI(client), calls


def test_poke_fresh_numeric_identity_prevents_wrong_target_submission(monkeypatch):
    provider, calls = poke_provider(monkeypatch, number="99999")
    with pytest.raises(TargetMismatch):
        provider.poke_member("34567", "peer", expected_user_id="23456")
    assert not calls


@pytest.mark.parametrize("received,existing", [(False, False), (True, True)])
def test_poke_requires_new_system_proof_and_never_replays_uncertain_submission(
    monkeypatch, received, existing
):
    provider, calls = poke_provider(monkeypatch, received=received, existing=existing)
    with pytest.raises(MutationOutcomeUnknown):
        provider.poke_member("34567", "peer", expected_user_id="23456")
    assert calls == ["submit"]


def test_poke_receipt_contains_the_actual_new_system_row(monkeypatch):
    provider, calls = poke_provider(monkeypatch)
    receipt = provider.poke_member("34567", "peer", expected_user_id="23456")
    assert receipt.ui_message_id == "poke-row" and receipt.user_id == "23456"
    assert calls == ["submit"]
