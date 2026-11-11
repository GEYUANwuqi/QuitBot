import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.adapters.onebot.api import OneBotAdapter
from quitbot_backend._engine.domain.activities import (
    ActivityCursor,
    activity_message_key,
    group_notice,
    parse_system_cards,
)
from quitbot_backend._engine.models import Message, Node, Target


def text(value):
    return Node("TextControl", value)


def field(class_name, value):
    return Node(class_name=class_name, children=(text(value),))


def card(*, status="pending", comment="留言: QB-test"):
    actions = (
        (Node("ButtonControl", "同意"), Node("ButtonControl", "拒绝"))
        if status == "pending"
        else (text(status),)
    )
    return Node(
        class_name="list-item",
        children=(
            field("card-title card-title__name", "peer"),
            field("secondary-info", "21:09"),
            field("card-summary-words", "申请加入"),
            field("card-summary-words card-summary__name", "测试群"),
            field("card-summary__postscript", comment),
            Node(class_name="sys-notify-card__action-wrapper", children=actions),
        ),
    )


def test_card_identity_survives_decision_but_distinguishes_verification_text():
    before = parse_system_cards(card(), "group")[0]
    after = parse_system_cards(card(status="已同意"), "group")[0]
    other = parse_system_cards(card(comment="another request"), "group")[0]
    assert before.ui_key == after.ui_key != other.ui_key
    assert before.status == "pending" and after.status == "已同意"
    assert before.group_name == "测试群" and before.summary == "申请加入"
    assert before.ui_key.startswith("uia:")
    assert not hasattr(before, "user_id")


def test_friend_request_uses_name_description_and_secondary_decision_control():
    root = Node(
        class_name="list-item",
        children=(
            field("card-desc card-desc__name", "peer"),
            field("card-desc", "请求加为好友"),
            field("secondary-info", "21:32"),
            text("留言：unique-test"),
            field("q-selected-area", "同意"),
            Node(class_name="q-select-button-action__wrapper"),
        ),
    )
    parsed = parse_system_cards(root, "friend")[0]
    assert parsed.actor_name == "peer" and parsed.comment == "unique-test"
    assert parsed.status == "pending" and parsed.actions == ("同意", "拒绝")
    rejected = replace(root, children=root.children[:-2] + (text("已拒绝"),))
    after = parse_system_cards(rejected, "friend")[0]
    assert after.status == "已拒绝" and after.ui_key == parsed.ui_key


def test_activity_cursor_suppresses_history_and_duplicate_rows_in_one_poll():
    cursor = ActivityCursor()
    assert cursor.take(["old"], lambda x: x) == []
    assert cursor.take(["old", "new", "new"], lambda x: x) == ["new"]
    assert cursor.take(["new"], lambda x: x) == []


def test_recall_is_detected_when_existing_message_id_changes_into_system_row():
    cursor = ActivityCursor()
    original = Message("same", "12345", "peer", "text", "incoming")
    recalled = replace(
        original,
        sender=None,
        text="08:18peer撤回了一条消息",
        direction="system",
        kind="system",
    )
    assert cursor.take([original], activity_message_key) == []
    assert cursor.take([recalled], activity_message_key) == [recalled]
    assert (
        cursor.take(
            [replace(recalled, text="peer撤回了一条消息")], activity_message_key
        )
        == []
    )


def test_recall_notices_reuse_scoped_message_ids_without_guessing_admin_actions():
    group, peer = Target("12345", "测试群"), Target("34567", "peer", "private")
    adapter = OneBotAdapter(
        SimpleNamespace(targets=(group, peer)),
        23456,
        member_ids={(group.id, "peer"): 34567},
    )
    try:
        original = Message("ui-row", group.id, "peer", "text", "incoming")
        number = adapter.message_event(group, original)["message_id"]
        recalled = replace(
            original, direction="system", kind="system", text="08:18peer撤回了一条消息"
        )
        event = adapter.notice_event(group, recalled)
        assert event["notice_type"] == "group_recall" and event["message_id"] == number
        assert event["operator_id"] == event["user_id"] == 34567
        private = replace(recalled, conversation_id=peer.id, text="对方撤回了一条消息")
        adapter.message_event(peer, replace(original, conversation_id=peer.id))
        event = adapter.notice_event(peer, private)
        assert event["notice_type"] == "friend_recall" and event["user_id"] == 34567
        assert "operator_id" not in event and "group_id" not in event
        assert (
            adapter.notice_event(
                peer, replace(private, text="你撤回了一条消息 重新编辑")
            )
            is None
        )
        assert (
            adapter.notice_event(
                group, replace(recalled, text="admin撤回了peer的一条消息")
            )
            is None
        )
        assert adapter.notice_event(group, replace(recalled, id="unobserved")) is None
        unresolved = replace(original, id="spoof", sender="08:18peer")
        assert adapter.message_event(group, unresolved)["post_type"] == "quitbot"
        assert adapter.notice_event(group, replace(recalled, id="spoof")) is None
    finally:
        adapter.index.close()


def test_async_activity_polls_private_scope_and_detects_changed_existing_row():
    from quitbot_backend._engine.async_api import AsyncQQInstance

    async def run():
        qq = AsyncQQInstance(initialize_com=False)
        scope = SimpleNamespace(id="34567", target=Target("34567", "peer", "private"))
        qq.conversation = lambda _: scope
        calls = []

        async def read(*_):
            calls.append(True)
            return (
                [Message("same", scope.id, "peer", "text", "incoming")]
                if len(calls) == 1
                else [
                    Message(
                        "same",
                        scope.id,
                        None,
                        "对方撤回了一条消息",
                        "system",
                        kind="system",
                    )
                ]
            )

        qq._call = read
        events = qq.activities([scope.id], kinds=(), mode="switch", interval=0.2)
        try:
            event = await asyncio.wait_for(anext(events), 1)
            assert (
                event["kind"] == "system_message" and event["target"].kind == "private"
            )
            assert len(calls) == 2
        finally:
            await events.aclose()
            await qq.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("raw", "subtype", "duration"),
    [
        ("21:09你邀请peer加入了群聊。", "invite", None),
        ("peer被你禁言10分钟", "ban", 600),
        ("peer被你解除禁言", "lift_ban", 0),
        ("21:09peer 回应了你的 消息: 🎉", "reaction", None),
    ],
)
def test_standard_notice_requires_system_row_and_complete_actor_binding(
    raw, subtype, duration
):
    message = Message(
        "row-1", "20001", None, raw, "system", timestamp="21:09", kind="system"
    )
    event = group_notice(message, 10001, {"peer": 30001})
    if subtype == "reaction":
        assert event["post_type"] == "quitbot"
        assert event["detail_type"] == "group_reaction_observation"
        assert event["target_id"] == 10001
        assert event["source_message_id"] is None and event["emoji_id"] is None
        assert event["emoji_text"] == "🎉"
        assert group_notice(replace(message, text=raw.removesuffix("🎉")), 10001,
                            {"peer": 30001})["emoji_text"] is None
        assert "message_id" not in event
    else:
        assert event["post_type"] == "notice" and event["sub_type"] == subtype
        assert event["operator_id"] == 10001
    assert event["user_id"] == 30001
    assert event.get("duration") == duration
    assert (
        group_notice(
            replace(message, direction="incoming", kind="text"),
            10001,
            {"peer": 30001},
        )
        is None
    )
    assert group_notice(message, 10001, {}) is None


def test_unexplained_member_disappearance_is_not_fabricated_as_a_kick_notice():
    message = Message(
        "row", "20001", None, "peer不在当前名册", "system", kind="system"
    )
    assert group_notice(message, 10001, {"peer": 30001}) is None


def test_request_mapping_is_instance_scoped_and_foreign_group_is_ignored():
    qq = SimpleNamespace(
        targets=[Target("20001", "测试群"), Target("30001", "peer", "private")]
    )
    adapter = OneBotAdapter(
        qq, 10001, member_ids={("20001", "peer"): 30001}
    )
    request = parse_system_cards(card(), "group")[0]
    try:
        event = adapter.notification_event(request)
        assert event["request_type"] == "group" and event["group_id"] == 20001
        assert event["flag"] == request.ui_key
        assert (
            adapter.notification_event(replace(request, group_name="foreign")) is None
        )
        assert adapter.notification_event(replace(request, status="已同意")) is None
        other = OneBotAdapter(qq, 12345)
        try:
            response = asyncio.run(
                other.handle(
                    {
                        "action": "set_group_add_request",
                        "params": {"flag": event["flag"]},
                    }
                )
            )
            assert response["retcode"] != 0
        finally:
            other.index.close()
    finally:
        adapter.index.close()
