import asyncio
from types import SimpleNamespace

from quitbot_backend._engine.adapters.onebot.api import OneBotAdapter
from quitbot_backend._engine.domain.controls import (
    GroupPreferences,
    MemberProfile,
    MessageMutationReceipt,
    SettingReceipt,
)
from quitbot_backend._engine.models import GroupMemberInfo, Target


def test_new_actions_resolve_scoped_message_ids_and_group_types():
    async def run():
        calls = []

        class Scope:
            id = "12345"
            target = Target("12345", "测试群")

            async def recall(self, ui_id, **kwargs):
                calls.append(("recall", ui_id, kwargs))
                return MessageMutationReceipt(
                    self.id, ui_id, "recall", "present", "recalled"
                )

            async def set_essence(self, ui_id, enabled):
                calls.append(("essence", ui_id, enabled))

            async def essence_state(self, ui_id):
                return True

            async def preferences(self):
                return GroupPreferences(self.id, False, 0, "silent")

            async def set_whole_ban(self, value):
                if not isinstance(value, bool):
                    raise TypeError("enable must be bool")
                calls.append(("whole", value))
                return SettingReceipt(self.id, "whole_banned", False, value)

        scope = Scope()
        qq = SimpleNamespace(account="actor", conversation=lambda _: scope)
        adapter = OneBotAdapter(qq, 23456)
        try:
            message_id = adapter.index.register(23456, scope.target, "opaque-1")
            for action in ("set_essence_msg", "delete_essence_msg", "delete_msg"):
                result = await adapter.handle(
                    {"action": action, "params": {"message_id": message_id}}
                )
                assert result["retcode"] == 0
            assert calls[:3] == [
                ("essence", "opaque-1", True),
                ("essence", "opaque-1", False),
                ("recall", "opaque-1", {"expected_text": None}),
            ]
            before = list(calls)
            wrong = await adapter.handle(
                {"action": "delete_msg", "params": {"message_id": message_id + 1}}
            )
            assert wrong["retcode"] == 1400 and calls == before
            wrong = await adapter.handle(
                {
                    "action": "x_set_auto_download",
                    "params": {"target_id": scope.id, "enabled": True},
                }
            )
            assert wrong["retcode"] == 1400 and calls == before
            good = await adapter.handle(
                {
                    "action": "set_group_whole_ban",
                    "params": {"group_id": 12345, "enable": True},
                }
            )
            assert good["retcode"] == 0 and calls[-1] == ("whole", True)
            bad = await adapter.handle(
                {
                    "action": "set_group_whole_ban",
                    "params": {"group_id": 12345, "enable": "true"},
                }
            )
            assert bad["retcode"] == 1400 and calls[-1] == ("whole", True)
        finally:
            adapter.index.close()

    asyncio.run(run())


def test_standard_ban_checks_fresh_numeric_profile_and_duration_before_mutation():
    async def run():
        calls, actual_id = [], ["34567"]

        class Scope:
            id = "12345"
            target = Target("12345", "测试群")

            async def members(self):
                return [GroupMemberInfo("peer", "member")]

            async def member_profile(self, name):
                return MemberProfile(self.id, name, actual_id[0], "member", ())

            async def set_member_ban(self, name, duration):
                calls.append((name, duration))

        scope = Scope()
        adapter = OneBotAdapter(
            SimpleNamespace(account="actor", conversation=lambda _: scope),
            23456,
            member_ids={("12345", "peer"): 34567},
        )
        try:
            params = {"group_id": 12345, "user_id": 34567, "duration": 1800}
            good = await adapter.handle({"action": "set_group_ban", "params": params})
            assert (
                good["retcode"] == 0
                and good["data"] is None
                and calls == [("peer", 1800)]
            )
            actual_id[0] = "45678"
            bad = await adapter.handle({"action": "set_group_ban", "params": params})
            assert bad["retcode"] == 1400 and calls == [("peer", 1800)]
            unsupported = await adapter.handle(
                {"action": "set_group_ban", "params": {**params, "duration": 30}}
            )
            assert unsupported["retcode"] == 1404 and calls == [("peer", 1800)]
        finally:
            adapter.index.close()

    asyncio.run(run())
