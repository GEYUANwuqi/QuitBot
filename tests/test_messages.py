from quitbot_backend._engine.models import (
    MessageCursor,
    Node,
    editor_text,
    parse_history,
    parse_messages,
)


def message(mid, text, *, own=False):
    return Node(
        class_name="ml-item",
        automation_id=mid,
        children=(
            Node(
                class_name="message-container message-container--self"
                if own
                else "message-container",
                children=(
                    Node(name="测试发送者", class_name="avatar-span"),
                    Node(
                        class_name="user-name",
                        children=(Node(type="TextControl", name="群主"),),
                    ),
                    Node(
                        class_name="message-content",
                        children=(Node(type="TextControl", name=text),),
                    ),
                ),
            ),
        ),
    )


def test_identical_text_is_preserved_by_message_id():
    root = Node(children=(message("101", "相同文本"), message("102", "相同文本")))
    parsed = parse_messages(root, "group-1")
    cursor = MessageCursor()
    cursor.prime(parsed[:1])
    assert [m.id for m in cursor.take(parsed)] == ["102"]
    assert cursor.take(parsed) == []
    assert [m.id for m in parsed] == ["101", "102"]


def test_sender_badges_and_draft_are_excluded():
    root = Node(
        children=(
            message("101", "正文", own=True),
            Node(
                class_name="ExEditor-qq-msg-editor",
                children=(Node(type="TextControl", name="草稿"),),
            ),
        )
    )
    parsed = parse_messages(root, "group-1")
    assert len(parsed) == 1
    assert parsed[0].text == "正文"
    assert parsed[0].sender == "测试发送者"
    assert parsed[0].direction == "outgoing"


def test_ids_are_scoped_to_conversation():
    cursor = MessageCursor()
    root = Node(children=(message("101", "正文"),))
    cursor.prime(parse_messages(root, "one"))
    assert len(cursor.take(parse_messages(root, "two"))) == 1


def test_link_text_and_inline_images_remain_in_body():
    root = Node(
        children=(
            Node(
                class_name="ml-item",
                automation_id="103",
                children=(
                    Node(name="测试发送者", class_name="avatar-span"),
                    Node(
                        class_name="message-content",
                        children=(
                            Node(type="TextControl", name="链接 "),
                            Node(
                                children=(
                                    Node(
                                        type="TextControl", name="https://example.com"
                                    ),
                                )
                            ),
                            Node(type="ImageControl", name="图片"),
                        ),
                    ),
                ),
            ),
        )
    )
    result = parse_messages(root, "one")[0]
    assert result.text == "链接 https://example.com"
    assert result.kind == "mixed"


def test_partial_virtualized_items_without_id_are_skipped():
    assert parse_messages(Node(class_name="ml-item"), "one") == []


def test_editor_preserves_anonymous_paragraph_boundaries():
    editor = Node(
        class_name="ProseMirror ExEditor-qq-msg-editor",
        children=(
            Node(
                type="GroupControl", children=(Node(type="TextControl", name="第一行"),)
            ),
            Node(
                type="GroupControl", children=(Node(type="TextControl", name="第二行"),)
            ),
        ),
    )
    assert editor_text(editor) == "第一行\n第二行"


def test_history_joins_character_nodes_without_inventing_message_ids():
    root = Node(
        children=(
            Node(
                class_name="record-msg-date",
                children=(Node(type="TextControl", name="2024/01/02"),),
            ),
            Node(
                class_name="record-msg-detail",
                children=(
                    Node(name="测试好友B", class_name="avatar-span"),
                    Node(type="TextControl", name="14:00"),
                    Node(
                        class_name="message-content",
                        children=(
                            Node(type="TextControl", name="中"),
                            Node(type="TextControl", name="文"),
                        ),
                    ),
                ),
            ),
        )
    )
    record = parse_history(root, "30002")[0]
    assert record.text == "中文"
    assert record.timestamp == "2024/01/02 14:00"
    assert record.sender == "测试好友B"
    assert record.id is None


def test_history_file_names_are_not_empty_search_results_and_have_no_protocol_ids():
    root = Node(
        children=(
            Node(
                class_name="record-msg-detail",
                children=(
                    Node(name="peer", class_name="avatar-span"),
                    Node(
                        class_name="file-element",
                        children=(
                            Node(
                                class_name="file-name",
                                children=(
                                    Node(type="TextControl", name="test"),
                                    Node(type="TextControl", name=".txt"),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )
    record = parse_history(root, "12345")[0]
    assert record.text == "test.txt" and record.kind == "file"
    assert record.attachments[0].name == "test.txt" and record.id is None
