"""Replay sanitized, reduced AT-SPI evidence without a running desktop."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from quitbot_backend._engine.infrastructure.atspi import snapshot as ADAPTER
from quitbot_backend._engine.models import MessageCursor, parse_messages

FIXTURE = Path(__file__).parent / "fixtures" / "linux_atspi_messages.json"


def snapshot():
    return json.loads(FIXTURE.read_text())


def test_atspi_messages_keep_identity_and_multiline_semantics():
    root = ADAPTER.normalize_chat(snapshot(), "测试群")
    messages = parse_messages(root, "20001")
    assert [m.text for m in messages] == ["相同文本", "相同文本", "第一行\n第二行🙂"]
    assert len({m.id for m in messages}) == 3
    assert all(m.sender == "测试好友A" and m.direction == "incoming" for m in messages)
    cursor = MessageCursor()
    cursor.prime(messages[:1])
    assert [m.id for m in cursor.take(messages)] == [m.id for m in messages[1:]]
    assert cursor.take(messages) == []


def test_private_sender_uses_verified_peer_without_crossing_frames():
    root = ADAPTER.normalize_chat(snapshot(), "测试好友A")
    messages = parse_messages(root, "30001", peer_name="测试好友A")
    assert len(messages) == 3
    assert all(
        m.sender == "测试好友A" and m.conversation_id == "30001" for m in messages
    )
    assert messages[0].id == "9000000000000000004"


@pytest.mark.parametrize("flag", ["truncated", "errors"])
def test_partial_snapshots_are_not_reported_as_complete(flag):
    data = snapshot()
    data[flag] = True if flag == "truncated" else [{"error": "provider disappeared"}]
    with pytest.raises(ValueError, match="Incomplete snapshot"):
        ADAPTER.normalize_chat(data, "测试群")


def test_missing_message_id_cannot_silently_drop_duplicate_text():
    data = snapshot()
    row = next(n for n in data["nodes"] if n["attributes"].get("class") == "ml-item")
    del row["attributes"]["id"]
    with pytest.raises(ValueError, match="no DOM id"):
        ADAPTER.normalize_chat(data, "测试群")


def test_ambiguous_chat_requires_explicit_frame_selection():
    data = snapshot()
    header = next(n for n in data["nodes"] if n["name"] == "测试群")
    duplicate = deepcopy(header)
    duplicate["path"] = [0, 2, 0]
    data["nodes"].append(duplicate)
    with pytest.raises(ValueError, match="found 2"):
        ADAPTER.normalize_chat(data, "测试群")
    assert ADAPTER.normalize_chat(data, "测试群", (0, 0))
