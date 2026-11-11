from pathlib import Path

import pytest
from uia_fixtures import load_tree

from quitbot_backend._engine.models import (
    Node,
    editor_has_content,
    editor_text,
    parse_messages,
)
from quitbot_backend._engine.operations import local_media_path


def test_quote_body_is_separate_from_the_original_message():
    root = Node(
        class_name="ml-item",
        automation_id="1",
        children=(
            Node(name="测试账号", class_name="avatar-span"),
            Node(
                class_name="message-content reply-message__inner",
                children=(
                    Node(
                        class_name="reply-element",
                        children=(
                            Node(type="TextControl", name="测试好友B"),
                            Node(type="TextControl", name="原文"),
                        ),
                    ),
                    Node(type="TextControl", name="回复正文"),
                ),
            ),
        ),
    )
    message = parse_messages(root, "group")[0]
    assert message.text == "回复正文"
    assert message.quote.sender == "测试好友B" and message.quote.text == "原文"
    assert message.quote.id is None


def test_file_bubble_is_not_misclassified_as_system_text():
    root = Node(
        class_name="ml-item",
        automation_id="1",
        children=(
            Node(class_name="message-container--self"),
            Node(
                class_name="file-element",
                children=(
                    Node(
                        class_name="file-name",
                        children=(
                            Node(type="TextControl", name="test."),
                            Node(type="TextControl", name="txt"),
                        ),
                    ),
                    Node(type="TextControl", name="64 B 已发送"),
                ),
            ),
        ),
    )
    message = parse_messages(root, "group")[0]
    assert message.kind == "file" and message.text == "test.txt"
    assert message.attachments[0].name == "test.txt"
    assert "已发送" in message.attachments[0].status


def test_quote_draft_cannot_be_overwritten_by_ordinary_send():
    editor = Node(
        children=(
            Node(
                class_name="editor-el__reply-wrapper",
                children=(Node(type="TextControl", name="引用原文"),),
            ),
            Node(type="TextControl", name="\n"),
        )
    )
    assert editor_text(editor).strip() == ""
    assert editor_has_content(editor)


def test_opaque_image_draft_cannot_be_mistaken_for_empty_text():
    assert editor_has_content(
        Node(children=(Node(class_name="editor-el--inline-block"),))
    )
    assert not editor_has_content(Node(children=(Node(type="TextControl", name="\n"),)))


def test_missing_and_invalid_media_are_rejected_before_ui(tmp_path):
    with pytest.raises(FileNotFoundError):
        local_media_path(tmp_path / "missing.png", "image")
    file = tmp_path / "data.txt"
    file.write_text("test")
    with pytest.raises(ValueError):
        local_media_path(file, "image")
    assert local_media_path(file, "file") == Path(file).resolve()


def test_quote_images_and_mention_icons_are_not_new_attachments():
    root = Node(
        class_name="ml-item",
        automation_id="1",
        children=(
            Node(
                class_name="message-content",
                children=(
                    Node(
                        class_name="reply-element",
                        children=(Node(type="ImageControl", name="图片"),),
                    ),
                    Node(type="ImageControl", name="", class_name="q-svg-icon"),
                    Node(type="TextControl", name="@测试好友B 回复"),
                ),
            ),
        ),
    )
    message = parse_messages(root, "group")[0]
    assert message.kind == "text"
    assert message.attachments == ()


def test_private_incoming_media_without_avatar_name_keeps_direction_and_peer():

    root = load_tree("video_file_message.json")
    message = parse_messages(root, "30001", peer_name="测试好友A")[0]
    assert message.direction == "incoming" and message.sender == "测试好友A"
    assert message.kind == "file" and message.text == "sample-video.mp4"
    assert message.attachments[0].name == "sample-video.mp4"
    assert "14天后过期" in message.attachments[0].status
