import asyncio
import time

from quitbot_backend._engine.adapters.onebot.api import OneBotAdapter
from quitbot_backend._engine.models import GroupMemberInfo, Message, SendReceipt, Target


class Scope:
    id = "12345"
    target = Target("12345", "测试群")

    def __init__(self):
        self.calls = []

    async def members(self, **kwargs):
        self.calls.append(("members", kwargs))
        return [GroupMemberInfo("display", "member", "34567", "nickname")]

    async def send_mention(self, member, text):
        self.calls.append(("mention", member, text))
        return SendReceipt(self.id, "opaque", "@display " + text)

    async def set_own_card(self, value):
        self.calls.append(("card", value))


class QQ:
    account = "bot"

    def __init__(self):
        self.scope = Scope()

    def conversation(self, key):
        assert str(key) == "12345"
        return self.scope


def test_native_at_is_validated_as_a_whole_and_uses_actual_member_number():
    async def run():
        qq = QQ()
        adapter = OneBotAdapter(qq, 23456)
        result = await adapter.handle(
            {
                "action": "send_group_msg",
                "params": {
                    "group_id": 12345,
                    "message": [
                        {"type": "at", "data": {"qq": "34567"}},
                        {"type": "text", "data": {"text": "hello"}},
                    ],
                },
            }
        )
        assert result["retcode"] == 0 and qq.scope.calls == [
            ("mention", "34567", "hello")
        ]
        before = list(qq.scope.calls)
        for invalid in (
            [
                {"type": "at", "data": {"qq": "all"}},
                {"type": "text", "data": {"text": "hello"}},
            ],
            [
                {"type": "at", "data": {"qq": "34567"}},
                {"type": "text", "data": {"text": "hello"}},
                {"type": "image", "data": {"file": "x.png"}},
            ],
            [
                {"type": "at", "data": {"qq": "34567"}},
                {"type": "text", "data": {"text": ""}},
            ],
        ):
            result = await adapter.handle(
                {
                    "action": "send_group_msg",
                    "params": {"group_id": 12345, "message": invalid},
                }
            )
            assert result["retcode"] != 0
        assert qq.scope.calls == before
        adapter.index.close()

    asyncio.run(run())


def test_uia_member_identity_is_scoped_and_expires_without_faking_a_sender():
    async def run():
        qq = QQ()
        adapter = OneBotAdapter(qq, 23456)
        message = Message("ui", "12345", "display", "hello", "incoming")
        assert adapter.message_event(qq.scope.target, message)["post_type"] == "quitbot"
        result = await adapter.handle(
            {
                "action": "get_group_member_info",
                "params": {"group_id": 12345, "user_id": 34567},
            }
        )
        assert (
            result["data"]["card"] == "display"
            and result["data"]["nickname"] == "nickname"
        )
        event = adapter.message_event(qq.scope.target, message)
        assert (
            event["user_id"] == 34567
            and event["_quitbot"]["sender_id_source"] == "uia_profile_name_match"
        )
        adapter._ui_member_ids[("12345", "display")] = (34567, time.monotonic() - 1)
        assert adapter.message_event(qq.scope.target, message)["post_type"] == "quitbot"
        adapter.index.close()

    asyncio.run(run())


def test_conflicting_explicit_identity_mapping_is_rejected():
    async def run():
        adapter = OneBotAdapter(QQ(), 23456, member_ids={("12345", "display"): 99999})
        result = await adapter.handle(
            {"action": "get_group_member_list", "params": {"group_id": 12345}}
        )
        assert result["retcode"] == 1400
        adapter.index.close()

    asyncio.run(run())


def test_group_card_endpoint_requires_bound_identity_before_editing_somebody_else():
    async def run():
        qq = QQ()
        adapter = OneBotAdapter(qq, 23456)
        result = await adapter.handle(
            {
                "action": "set_group_card",
                "params": {"group_id": 12345, "user_id": 34567, "card": "changed"},
            }
        )
        assert result["retcode"] == 1404 and qq.scope.calls == [("members", {})]
        qq.scope.calls.clear()
        result = await adapter.handle(
            {
                "action": "set_group_card",
                "params": {"group_id": 12345, "user_id": 23456, "card": "changed"},
            }
        )
        assert result["retcode"] == 0 and qq.scope.calls == [("card", "changed")]
        adapter.index.close()

    asyncio.run(run())
