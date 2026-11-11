from types import SimpleNamespace

import pytest

from quitbot_backend._engine.errors import InstanceClosed, TargetMismatch
from quitbot_backend._engine.instance import (
    Group,
    MessageEvent,
    MessageHandle,
    QQInstance,
)
from quitbot_backend._engine.models import Message, SendReceipt, Target


class FakeClient:
    def __init__(self, account):
        self.backend = SimpleNamespace(
            account=account, hwnd=hash(account), check_thread=lambda: None
        )
        self.targets = (Target("1", "测试群"), Target("2", "测试好友B", "private"))
        self.sent = []

    def target(self, key):
        key = key.id if isinstance(key, Target) else key
        matches = [t for t in self.targets if key in (t.id, t.name)]
        if len(matches) != 1:
            raise TargetMismatch("Unknown target")
        return matches[0]

    def send_text(self, target, text, **kwargs):
        self.sent.append((target.id, text))
        return SendReceipt(target.id, str(len(self.sent)), text)

    def watch(self, targets, **kwargs):
        yield Message("incoming", targets[0].id, "测试好友B", "ping", "incoming")


def test_handles_are_cached_within_each_instance_only():
    a = QQInstance.from_client(FakeClient("测试账号"))
    b = QQInstance.from_client(FakeClient("测试好友B"))
    assert isinstance(a.group("1"), Group)
    assert a.group("1") is a.group("测试群")
    assert a.group("1") is not b.group("1")
    with pytest.raises(TargetMismatch):
        a.conversation(b.group("1"))


def test_operations_route_to_the_bound_account():
    ca, cb = FakeClient("测试账号"), FakeClient("测试好友B")
    a, b = QQInstance.from_client(ca), QQInstance.from_client(cb)
    sent = a.group("1").send_text("a")
    b.group("1").send_text("b")
    assert sent.instance is a
    assert ca.sent == [("1", "a")] and cb.sent == [("1", "b")]


def test_close_invalidates_existing_handles_but_not_other_instances():
    a = QQInstance.from_client(FakeClient("测试账号"))
    b = QQInstance.from_client(FakeClient("测试好友B"))
    handle = a.group("1")
    subscription = a.on_message(lambda event: None)
    a.close()
    assert not subscription.active
    with pytest.raises(InstanceClosed):
        handle.send_text("closed")
    b.group("1").send_text("still available")
    assert not b.closed


def test_collection_rejects_wrong_conversation_kind():
    a = QQInstance.from_client(FakeClient("测试账号"))
    with pytest.raises(TargetMismatch):
        a.friend("1")
    assert a.friends.configured()[0].id == "2"


def test_event_reply_and_subscription_cancellation_are_instance_scoped():
    client = FakeClient("测试账号")
    a = QQInstance.from_client(client)
    group = a.group("1")
    called = []

    def handler(event):
        called.append(event.instance)
        event.reply("pong")

    first = a.on_message(handler, conversation=group)
    second = a.on_message(handler, conversation=group)
    second.cancel()
    a.run([group])
    assert first.active and not second.active
    assert called == [a]
    assert client.sent == [("1", "pong")]


def test_message_and_event_cannot_cross_conversation_boundaries():
    a = QQInstance.from_client(FakeClient("测试账号"))
    with pytest.raises(TargetMismatch):
        MessageHandle(
            a.group("1"), "x", data=Message("x", "2", "测试好友B", "hi", "incoming")
        )
    handle = MessageHandle(
        a.group("1"), "x", data=Message("x", "1", "测试好友B", "hi", "incoming")
    )
    with pytest.raises(TargetMismatch):
        MessageEvent(a.friend("2"), handle)
