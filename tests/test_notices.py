import asyncio
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.adapters.onebot.api import OneBotAdapter
from quitbot_backend._engine.domain.notices import GroupNotice, GroupNotices
from quitbot_backend._engine.errors import ControlUnavailable, TargetMismatch
from quitbot_backend._engine.infrastructure.uia.notices import (
    parse_notices,
    unique_notice,
)
from quitbot_backend._engine.models import Node


def fixture(name="测试群", info=("测试账号", "21:17"), body=True, media=False):
    def text(s):
        return Node(type="TextControl", name=s)

    return Node(
        children=(
            Node(class_name="notice-title", children=(text(name),)),
            Node(
                class_name="notice-list",
                children=(
                    Node(
                        class_name="notice-item",
                        children=(
                            Node(
                                class_name="info", children=tuple(text(s) for s in info)
                            ),
                            Node(
                                class_name="content__text" if body else "unknown",
                                children=(text("test公告"),),
                            ),
                            Node(type="ImageControl", name="toolbar icon"),
                            Node(
                                class_name="content__media",
                                children=(
                                    (Node(type="ImageControl"),) if media else ()
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )


@pytest.mark.parametrize("media", [False, True])
def test_notices_preserve_visible_text_and_do_not_invent_ids_or_server_time(media):
    result = parse_notices(fixture(media=media), "20001", "测试群")
    assert result.items == (
        GroupNotice("test公告", "测试账号", "21:17", media_present=media),
    )
    assert result.items[0].id is None and result.complete is False
    unknown = parse_notices(fixture(info=("ambiguous",)), "20001", "测试群")
    assert unknown.items[0].author_name is None and unknown.items[0].time_text is None


def test_notices_reject_wrong_group_and_unrecognized_rows():
    with pytest.raises(TargetMismatch):
        parse_notices(fixture("different"), "20001", "测试群")
    with pytest.raises(ControlUnavailable):
        parse_notices(fixture(body=False), "20001", "测试群")


def test_notice_selection_rejects_duplicate_text_and_changed_author_or_media():
    selected = GroupNotice("same body", "owner", "12:00")
    with pytest.raises(TargetMismatch):
        unique_notice(
            (selected, GroupNotice(selected.text, "other", "12:01")), selected.text
        )
    for changed in (
        GroupNotice(selected.text, "other", selected.time_text),
        GroupNotice(
            selected.text, selected.author_name, selected.time_text, media_present=True
        ),
    ):
        with pytest.raises(TargetMismatch):
            unique_notice((changed,), selected.text, selected)


def test_notice_extension_requires_group_and_serializes_ui_limits():
    async def run():
        target = SimpleNamespace(kind="group", id="20001")

        async def notices():
            return GroupNotices(target.id, (GroupNotice("test公告", "测试账号", "21:17"),))

        group = SimpleNamespace(target=target, id=target.id, notices=notices)
        adapter = OneBotAdapter(
            SimpleNamespace(account="测试账号", conversation=lambda _: group), 10001
        )
        try:
            response = await adapter.handle(
                {
                    "action": "x_get_group_notices",
                    "params": {"target_id": target.id},
                    "echo": 7,
                }
            )
            assert response["retcode"] == 0 and response["echo"] == 7
            assert (
                response["data"]["complete"] is False
                and response["data"]["items"][0]["id"] is None
            )
            target.kind = "private"
            response = await adapter.handle(
                {"action": "x_get_group_notices", "params": {"target_id": target.id}}
            )
            assert response["retcode"] != 0
        finally:
            adapter.index.close()

    asyncio.run(run())
