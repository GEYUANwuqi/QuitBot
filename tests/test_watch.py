from quitbot_backend._engine.client import QQClient
from quitbot_backend._engine.models import Message, Target


def test_partial_incoming_item_is_not_consumed_before_sender_arrives(monkeypatch):
    client = QQClient.__new__(QQClient)
    client.targets = (Target("1", "测试"),)
    partial = Message("m1", "1", None, "hello", "system")
    complete = Message("m1", "1", "测试好友B", "hello", "incoming")
    frames = iter([[], [partial], [complete]])
    client.read_messages = lambda target: next(frames)
    monkeypatch.setattr("quitbot_backend._engine.client.time.sleep", lambda seconds: None)
    assert next(client.watch(["1"], interval=0.2)) == complete


def test_sync_detached_watch_does_not_switch_main_sessions(monkeypatch):
    client = QQClient.__new__(QQClient)
    target = Target("1", "测试")
    client.targets = (target,)
    frames = iter([[], [Message("m1", "1", "测试好友B", "hello", "incoming")]])
    opened = []
    client.open_detached = opened.append
    client.read_detached = lambda scope: next(frames)
    client.read_messages = lambda scope: (_ for _ in ()).throw(
        AssertionError("switched")
    )
    monkeypatch.setattr("quitbot_backend._engine.client.time.sleep", lambda seconds: None)
    assert next(client.watch(["1"], mode="detached")).id == "m1"
    assert opened == [target]


def test_incoming_id_is_not_consumed_before_its_text_has_loaded(monkeypatch):
    client = QQClient.__new__(QQClient)
    client.targets = (Target("1", "测试"),)
    partial = Message("m1", "1", "sender", "", "incoming")
    complete = Message("m1", "1", "sender", "hello", "incoming")
    frames = iter([[], [partial], [complete]])
    client.read_messages = lambda target: next(frames)
    monkeypatch.setattr("quitbot_backend._engine.client.time.sleep", lambda seconds: None)
    assert next(client.watch(["1"])) == complete
