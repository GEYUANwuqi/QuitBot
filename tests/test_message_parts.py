from quitbot_backend._engine.adapters.onebot.api import OneBotAdapter
from quitbot_backend._engine.models import (
    Message,
    MessagePart,
    Node,
    Target,
    message_parts,
)


def test_native_at_parts_preserve_order_and_exclude_quote_preview():
    body = Node(
        children=(
            Node(type="TextControl", name="before "),
            Node(
                class_name="text-element--at",
                children=(Node(type="TextControl", name="@member"),),
            ),
            Node(type="TextControl", name=" after"),
            Node(
                class_name="reply-element",
                children=(Node(type="TextControl", name="old"),),
            ),
        )
    )
    assert message_parts(body) == (
        MessagePart("text", "before "),
        MessagePart("mention", "member"),
        MessagePart("text", " after"),
    )


def test_onebot_does_not_turn_plain_at_text_into_a_real_mention():
    adapter = OneBotAdapter(
        None, 23456, member_ids={("12345", "sender"): 34567, ("12345", "member"): 45678}
    )
    target = Target("12345", "测试群")
    plain = Message(
        "p",
        "12345",
        "sender",
        "@member hi",
        "incoming",
        parts=(MessagePart("text", "@member hi"),),
    )
    assert adapter.message_event(target, plain)["message"] == [
        {"type": "text", "data": {"text": "@member hi"}}
    ]
    native = Message(
        "n",
        "12345",
        "sender",
        "@member hi",
        "incoming",
        parts=(MessagePart("mention", "member"), MessagePart("text", " hi")),
    )
    assert adapter.message_event(target, native)["message"] == [
        {"type": "at", "data": {"qq": "45678"}},
        {"type": "text", "data": {"text": " hi"}},
    ]
    adapter.member_ids.pop(("12345", "member"))
    assert adapter.message_event(target, native)["reason"] == "mention_id_unavailable"
    adapter.index.close()
