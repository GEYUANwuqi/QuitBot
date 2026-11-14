import asyncio
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.adapters.onebot.api import OneBotAdapter
from quitbot_backend._engine.errors import ControlUnavailable
from quitbot_backend._engine.infrastructure.storage.media import ImageCache
from quitbot_backend._engine.models import (
    Attachment,
    Message,
    MessagePart,
    Node,
    Target,
    parse_messages,
)
from quitbot_backend._engine.ui.workflows.media import export_images, read_image_file

PNG = b"\x89PNG\r\n\x1a\nsynthetic-test-image"
GIF = b"GIF89asynthetic-animation"


def test_account_cache_rejects_cross_account_paths_symlinks_and_wrong_types(tmp_path):
    a, b = ImageCache(tmp_path / "a"), ImageCache(tmp_path / "b")
    name = a.put(GIF)
    assert a.get(name).read_bytes() == GIF
    with pytest.raises(ValueError, match="不存在"):
        b.get(name)
    for bad in ("../a/" + name, str(a.get(name)), "https://example.com/image.png"):
        with pytest.raises(ValueError, match="标识"):
            b.get(bad)
    try:
        (b.directory / name).symlink_to(a.get(name))
    except OSError:
        pytest.skip("Symlinks require platform permission")
    with pytest.raises(ValueError):
        b.get(name)
    with pytest.raises(ValueError):
        b.put(b"not an image")


def test_source_image_only_reads_ui_file_owned_by_current_user(tmp_path, monkeypatch):
    home = tmp_path / "user"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    path = home / "from-qq.png"
    path.write_bytes(PNG)
    assert read_image_file(path) == PNG
    outside = tmp_path / "private.png"
    outside.write_bytes(PNG)
    with pytest.raises(ControlUnavailable, match="用户目录"):
        read_image_file(outside)


def test_message_parts_keep_text_image_order_and_duplicate_images():
    body = Node(
        class_name="message-content",
        children=(
            Node(type="TextControl", name="前"),
            Node(type="ImageControl", class_name="pic-element", name="图片"),
            Node(type="TextControl", name="中"),
            Node(type="ImageControl", class_name="pic-element", name="图片"),
            Node(type="TextControl", name="后"),
        ),
    )
    message = parse_messages(
        Node(class_name="ml-item", automation_id="msg-1", children=(body,)), "30001"
    )[0]
    assert [p.kind for p in message.parts] == ["text", "image", "text", "image", "text"]
    assert len(message.attachments) == 2
    assert message.text == "前中后"


def test_onebot_image_event_get_image_get_msg_and_resend(tmp_path):
    async def scenario():
        target = Target("30001", "测试好友", "private")
        message = Message(
            "msg-1",
            target.id,
            target.name,
            "前后",
            "incoming",
            kind="mixed",
            attachments=(Attachment("image"),),
            parts=(
                MessagePart("text", "前"),
                MessagePart("image", ""),
                MessagePart("text", "后"),
            ),
        )
        sent = []

        class Scope:
            async def export_images(self, mid):
                assert mid == message.id
                return [GIF]

            async def get_message(self, mid):
                return message

            async def send_image(self, path):
                sent.append(path)
                return SimpleNamespace(message_id="msg-2")

        scope = Scope()
        scope.target, scope.id = target, target.id
        qq = SimpleNamespace(conversation=lambda _: scope)
        adapter = OneBotAdapter(qq, "10001", image_cache=ImageCache(tmp_path))
        await adapter.prepare_message(scope, message)
        event = adapter.message_event(target, message)
        assert event["post_type"] == "message"
        assert [p["type"] for p in event["message"]] == ["text", "image", "text"]
        name = event["message"][1]["data"]["file"]
        assert name.endswith(".gif") and "[CQ:image,file=" in event["raw_message"]
        result = await adapter.handle({"action": "get_image", "params": {"file": name}})
        assert result["retcode"] == 0 and result["data"]["file"].endswith(name)
        msg = await adapter.handle(
            {"action": "get_msg", "params": {"message_id": event["message_id"]}}
        )
        assert msg["retcode"] == 0 and msg["data"]["message"] == event["message"]
        sent_result = await adapter.handle(
            {
                "action": "send_private_msg",
                "params": {
                    "user_id": "30001",
                    "message": [{"type": "image", "data": {"file": name}}],
                },
            }
        )
        assert sent_result["retcode"] == 0 and sent == [str(tmp_path / name)]
        wrong = replace(message, conversation_id="30002")
        other = Target("30002", "其他测试好友", "private")
        assert adapter.message_event(other, wrong)["reason"] == "media_not_exportable"

    asyncio.run(scenario())


def test_clipboard_export_rejects_stale_or_wrong_image_count(monkeypatch):
    from quitbot_backend._engine.ui.workflows import media

    copied, closed = [], []
    control = SimpleNamespace(ControlTypeName="MenuItemControl", Name="复制")
    auto = SimpleNamespace(copied_files=lambda: copied)
    b = SimpleNamespace(auto=auto, walk=lambda _: [control], invoke=lambda _: None)
    ui = SimpleNamespace(
        checked=lambda _: nullcontext(),
        message_menu=lambda *_: object(),
        _dismiss_menu=lambda: closed.append(True),
    )
    client = SimpleNamespace(
        control_ui=ui,
        backend=b,
        get_message=lambda *_: SimpleNamespace(attachments=(Attachment("image"),)),
    )
    seq = iter([1, 2])
    monkeypatch.setattr(media, "clipboard_sequence", lambda _: next(seq))
    with pytest.raises(ControlUnavailable, match="数量"):
        export_images(client, Target("30001", "好友", "private"), "msg-1")
    assert closed == [True]


def test_file_download_checks_destination_and_exact_message_before_menu(tmp_path):
    from quitbot_backend._engine.errors import TargetMismatch
    from quitbot_backend._engine.ui.workflows.media import download_message_file

    calls = []
    ui = SimpleNamespace(
        checked=lambda _: nullcontext(), message_menu=lambda *_: calls.append("menu")
    )
    message = SimpleNamespace(attachments=(Attachment("file", "other.mp4"),))
    client = SimpleNamespace(
        control_ui=ui, backend=object(), get_message=lambda *_: message
    )
    target = Target("30001", "测试好友", "private")
    path = tmp_path / "new.mp4"
    with pytest.raises(TargetMismatch, match="expected_name"):
        download_message_file(client, target, "msg-1", path, expected_name="video.mp4")
    path.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        download_message_file(client, target, "msg-1", path, expected_name="video.mp4")
    assert calls == [] and path.read_bytes() == b"existing"


def test_cache_eviction_leaves_an_unresolved_message_instead_of_broken_event(tmp_path):
    target = Target("30001", "测试好友", "private")
    message = Message(
        "msg-1",
        target.id,
        target.name,
        "",
        "incoming",
        kind="image",
        attachments=(Attachment("image"),),
    )
    cache = ImageCache(tmp_path)
    adapter = OneBotAdapter(object(), "10001", image_cache=cache)
    name = cache.put(GIF)
    adapter._images[(target.kind, target.id, message.id)] = [name]
    cache.get(name).unlink()
    assert adapter.message_event(target, message)["reason"] == "media_not_exportable"


def test_face_ids_come_from_qq_clipboard_and_keep_duplicates():
    from quitbot_backend._engine.ui.workflows.media import face_ids_from_rich_text

    value = '<QQRichEditFormat><Info version="1001"/><EditElement type="2" sysfaceindex="14"/><EditElement type="2" sysfaceindex="14"/></QQRichEditFormat>'
    assert face_ids_from_rich_text(value, 2) == [14, 14]
    mixed = value.replace(
        "</QQRichEditFormat>",
        '<EditElement type="0"><![CDATA[测试 <文字>]]></EditElement></QQRichEditFormat>',
    )
    assert face_ids_from_rich_text(mixed, 2) == [14, 14]
    for bad, count in [
        (value, 1),
        (value.replace('sysfaceindex="14"', 'sysfaceindex="-1"'), 2),
        ("<!DOCTYPE x>" + value, 2),
        ("<QQRichEditFormat>", 1),
    ]:
        with pytest.raises(ControlUnavailable):
            face_ids_from_rich_text(bad, count)


def test_onebot_mixed_face_order_uses_explicit_ids():
    async def scenario():
        target = Target("30001", "测试好友", "private")

        class Scope:
            async def export_face_ids(self, mid):
                return [14, 0]

        scope = Scope()
        scope.target = target
        message = Message(
            "msg-1",
            target.id,
            target.name,
            "前中后",
            "incoming",
            kind="mixed",
            parts=(
                MessagePart("text", "前"),
                MessagePart("face", ""),
                MessagePart("text", "中"),
                MessagePart("face", ""),
                MessagePart("text", "后"),
            ),
            face_count=2,
        )
        adapter = OneBotAdapter(object(), "10001")
        await adapter.prepare_message(scope, message)
        event = adapter.message_event(target, message)
        assert event["post_type"] == "message"
        assert [p["type"] for p in event["message"]] == [
            "text",
            "face",
            "text",
            "face",
            "text",
        ]
        assert event["message"][1]["data"]["id"] == "14"
        assert event["message"][3]["data"]["id"] == "0"
        assert event["raw_message"] == "前[CQ:face,id=14]中[CQ:face,id=0]后"

    asyncio.run(scenario())
