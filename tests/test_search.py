import pytest

from quitbot_backend._engine.client import QQClient
from quitbot_backend._engine.models import Node, parse_search_results


def option(label, *, cls="search-result-item"):
    return Node(type="ListItemControl", name=label, class_name=cls)


def test_member_matches_do_not_inherit_contact_id():
    root = Node(
        children=(
            Node(type="TextControl", name="联系人"),
            option("测试好友B (30002) 来自: 我的好友"),
            Node(type="TextControl", name="群聊"),
            option("测试群 (3人) 包含： 30002"),
            Node(type="TextControl", name="聊天记录"),
            option("测试群 30002 的聊天记录"),
            option("30002", cls="related-search-list__item"),
        )
    )
    contact, group, history = parse_search_results(root)
    assert contact.id == "30002" and contact.name == "测试好友B"
    assert group.name == "测试群" and group.id is None
    assert (
        history.category == "聊天记录" and history.name is None and history.id is None
    )


def test_contact_nickname_parentheses_are_not_assumed_to_be_an_id():
    root = Node(
        children=(
            Node(type="TextControl", name="联系人"),
            option("测试好友C（备用） 来自: 我的好友"),
        )
    )
    result = parse_search_results(root)[0]
    assert result.name == "测试好友C（备用）" and result.id is None


def test_text_inside_a_result_does_not_change_section():
    root = Node(
        children=(
            Node(type="TextControl", name="聊天记录"),
            Node(
                type="ListItemControl",
                name="第一条",
                class_name="search-result-item",
                children=(Node(type="TextControl", name="联系人"),),
            ),
            option("第二条"),
        )
    )
    assert [r.category for r in parse_search_results(root)] == ["聊天记录", "聊天记录"]


@pytest.mark.parametrize("query", [" ", "abc\n", "x" * 101])
def test_search_rejects_invalid_queries_before_accessing_desktop(query):
    with pytest.raises(ValueError):
        QQClient.__new__(QQClient).search(query)
