import asyncio

import pytest
from uia_fixtures import load_tree

from quitbot_backend._engine.adapters.onebot.api import OneBotAdapter
from quitbot_backend._engine.domain.onebot import parse_segments
from quitbot_backend._engine.errors import SendOutcomeUnknown
from quitbot_backend._engine.infrastructure.storage.message_index import MessageIndex
from quitbot_backend._engine.models import (
    Attachment,
    Message,
    Node,
    SendReceipt,
    Target,
    message_ready,
    parse_messages,
)


class Scope:
    def __init__(self, kind="group"):
        self.target = Target("12345", "测试群", kind)
        self.id = self.target.id
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)
        return SendReceipt(self.id, "7777777777777777777", text)

    async def reply(self, message_id, text):
        self.sent.append((message_id, text))
        return SendReceipt(self.id, "8888888888888888888", text)


class FakeQQ:
    account = "bot"

    def __init__(self, scope=None):
        self.scope = scope or Scope()
        self.targets = [self.scope.target]

    def conversation(self, key):
        if str(key) != self.scope.id:
            raise ValueError("unknown target")
        return self.scope


def test_persistent_int32_mapping_is_account_and_conversation_scoped(tmp_path):
    path = tmp_path / "ids.sqlite3"
    target = Target("12345", "测试群")
    index = MessageIndex(path)
    number = index.register(1, target, "7777777777777777777")
    other = index.register(2, target, "7777777777777777777")
    assert 0 < number < 2**31 and number != other
    with pytest.raises(ValueError):
        index.resolve(2, number)
    index.close()
    index = MessageIndex(path)
    assert index.register(1, target, "7777777777777777777") == number
    assert index.resolve(1, number) == ("group", "12345", "7777777777777777777")
    index.close()


def test_ob11_echo_text_and_quote_route_to_opaque_ui_id():
    async def run():
        qq = FakeQQ()
        adapter = OneBotAdapter(qq, 23456)
        response = await adapter.handle(
            {
                "action": "send_group_msg",
                "params": {
                    "group_id": 12345,
                    "message": [{"type": "text", "data": {"text": "literal {Enter}"}}],
                },
                "echo": {"id": [1, False]},
            }
        )
        assert response["retcode"] == 0 and response["echo"] == {"id": [1, False]}
        assert qq.scope.sent == ["literal {Enter}"]
        number = response["data"]["message_id"]
        response = await adapter.handle(
            {
                "action": "send_group_msg",
                "params": {
                    "group_id": 12345,
                    "message": f"[CQ:reply,id={number}]reply",
                },
            }
        )
        assert response["retcode"] == 0
        assert qq.scope.sent[-1] == ("7777777777777777777", "reply")
        adapter.index.close()

    asyncio.run(run())


def test_unsupported_message_is_rejected_before_partial_send():
    async def run():
        qq = FakeQQ()
        adapter = OneBotAdapter(qq, 23456)
        response = await adapter.handle(
            {
                "action": "send_group_msg",
                "params": {"group_id": 12345, "message": "hello[CQ:at,qq=22222]"},
            }
        )
        assert response["retcode"] == 1404 and not qq.scope.sent
        response = await adapter.handle({"action": "delete_msg", "echo": None})
        assert response["retcode"] == 1400 and "echo" in response
        assert (await adapter.handle([]))["retcode"] == 1400
        adapter.index.close()

    asyncio.run(run())


def test_uncertain_send_preserves_echo_and_never_retries():
    async def run():
        class UncertainScope(Scope):
            async def send_text(self, text):
                self.sent.append(text)
                raise SendOutcomeUnknown("unknown")

        qq = FakeQQ(UncertainScope())
        adapter = OneBotAdapter(qq, 23456)
        response = await adapter.handle(
            {
                "action": "send_group_msg",
                "params": {"group_id": 12345, "message": "once"},
                "echo": [1],
            }
        )
        assert response["retcode"] == 101 and not response["data"]["retryable"]
        assert response["echo"] == [1] and qq.scope.sent == ["once"]
        invalid = await adapter.handle(
            {"action": "send_msg", "params": [], "echo": {"bad": True}}
        )
        assert invalid["retcode"] == 1400 and invalid["echo"] == {"bad": True}
        adapter.index.close()

    asyncio.run(run())


def test_unresolved_group_sender_is_an_explicit_extension_not_fake_user_id():
    target = Target("12345", "测试群")
    message = Message("ui", target.id, "same name", "hello", "incoming")
    adapter = OneBotAdapter(FakeQQ(), 23456)
    event = adapter.message_event(target, message)
    assert event["post_type"] == "quitbot" and "user_id" not in event
    adapter.member_ids[(target.id, "same name")] = 34567
    event = adapter.message_event(target, message)
    assert event["post_type"] == "message" and event["user_id"] == 34567
    assert event["group_id"] == 12345 and isinstance(event["message_id"], int)
    adapter.index.close()


def test_private_event_is_standard_and_unknown_media_is_not_fabricated():
    adapter = OneBotAdapter(FakeQQ(), 23456)
    target = Target("12345", "friend", "private")
    event = adapter.message_event(
        target, Message("ui1", target.id, "friend", "[hi]", "incoming")
    )
    assert event["user_id"] == 12345 and "group_id" not in event
    assert event["raw_message"] == "&#91;hi&#93;"
    media = Message(
        "ui2",
        target.id,
        "friend",
        "",
        "incoming",
        kind="image",
        attachments=(Attachment("image"),),
    )
    event = adapter.message_event(target, media)
    assert event["post_type"] == "quitbot" and event["reason"] == "media_not_exportable"
    adapter.index.close()


def test_observed_forward_card_is_deliverable_without_fabricating_forward_id():
    root = load_tree("forward_message.json")
    target = Target("10001", "测试账号", "private")
    message = parse_messages(root, target.id, peer_name=target.name)[0]
    assert message.kind == "forward" and message.direction == "incoming"
    assert message_ready(message)
    assert message.text.startswith("群聊的聊天记录")
    assert "第一条示例消息" in message.text and "第二条示例消息" in message.text
    assert message.timestamp not in message.text
    adapter = OneBotAdapter(FakeQQ(), 30001)
    try:
        event = adapter.message_event(target, message)
        assert event["post_type"] == "quitbot"
        assert event["reason"] == "forward_id_unavailable"
        assert event["ui_message"]["kind"] == "forward" and "message" not in event
    finally:
        adapter.index.close()


def test_observed_random_sticker_is_deliverable_without_fabricating_result():
    root = load_tree("random_sticker_message.json")
    target = Target("20001", "测试群", "group")
    message = parse_messages(root, target.id)[0]
    assert message.kind == "random_sticker" and message.direction == "incoming"
    assert message.text == "" and message_ready(message)
    adapter = OneBotAdapter(
        FakeQQ(), 30001, member_ids={(target.id, "测试账号"): "10001"}
    )
    try:
        event = adapter.message_event(target, message)
        assert event["post_type"] == "quitbot"
        assert event["reason"] == "random_sticker_details_unavailable"
        assert (
            event["ui_message"]["kind"] == "random_sticker" and "message" not in event
        )
    finally:
        adapter.index.close()


@pytest.mark.parametrize(
    "fixture,target,expected",
    [
        ("contact_card_message", Target("20001", "测试群", "group"),
         ("测试账号推荐好友", "outgoing", "10:00")),
        ("flash_folder_card_message", Target("10001", "测试账号", "private"),
         ("sample-folder80.00\u2009B · 2 项 · 14天后过期QQ闪传", "incoming", "10:01")),
    ],
)
def test_observed_cards_keep_visible_text_without_fabricating_payload(fixture, target, expected):
    from quitbot_backend._engine.errors import ControlUnavailable
    from quitbot_backend._engine.ui.workflows.sharing import contact_card_name

    root = load_tree(fixture + ".json")
    message = parse_messages(root, target.id, peer_name=target.name)[0]
    if fixture == "contact_card_message":
        assert contact_card_name(root) == "测试账号"
        with pytest.raises(ControlUnavailable):
            contact_card_name(Node(children=(root, root)))
    else:
        with pytest.raises(ControlUnavailable):
            contact_card_name(root)
    assert message.kind == "card" and message_ready(message)
    assert (message.text, message.direction, message.timestamp) == expected
    assert message.timestamp not in message.text and not message.parts
    adapter = OneBotAdapter(FakeQQ(), 30001)
    try:
        event = adapter.message_event(target, message)
        assert event["post_type"] == "quitbot"
        assert event["reason"] == "card_payload_unavailable"
        assert event["ui_message"]["kind"] == "card" and "message" not in event
    finally:
        adapter.index.close()


def test_cq_escaping_and_auto_escape_do_not_execute_literal_segments():
    assert parse_segments("&#91;CQ:at,qq=all&#93;")[0].data["text"] == "[CQ:at,qq=all]"
    assert (
        parse_segments("[CQ:at,qq=all]", auto_escape=True)[0].data["text"]
        == "[CQ:at,qq=all]"
    )
    assert parse_segments("a&amp;b&#91;c&#93;")[0].data["text"] == "a&b[c]"
