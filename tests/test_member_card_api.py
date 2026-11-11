import asyncio
from types import SimpleNamespace

from quitbot_backend._engine.adapters.onebot.api import OneBotAdapter
from quitbot_backend._engine.domain.member_interactions import MemberCardReceipt
from quitbot_backend._engine.errors import MutationOutcomeUnknown
from quitbot_backend._engine.models import GroupMemberInfo, Target


def test_standard_member_card_updates_name_binding_for_roundtrip_and_returns_null():
    async def run():
        calls = []

        class Scope:
            id = "12345"
            target = Target(id, "测试群")
            name = "peer"

            async def members(self):
                return [GroupMemberInfo(self.name, "member")]

            async def set_member_card(self, name, value, *, expected_user_id):
                assert name == self.name and expected_user_id == "34567"
                calls.append((name, value, expected_user_id))
                self.name = value
                return MemberCardReceipt(
                    self.id, expected_user_id, name, value, name, value
                )

        scope = Scope()
        adapter = OneBotAdapter(
            SimpleNamespace(conversation=lambda _: scope),
            23456,
            member_ids={("12345", "peer"): 34567, ("99999", "peer"): 34567},
        )
        try:
            for value in ("new", "peer"):
                response = await adapter.handle(
                    {
                        "action": "set_group_card",
                        "params": {"group_id": 12345, "user_id": 34567, "card": value},
                    }
                )
                assert response["retcode"] == 0 and response["data"] is None
                assert adapter._ui_member_ids[("12345", value)][0] == 34567
            assert calls == [("peer", "new", "34567"), ("new", "peer", "34567")]
            assert ("12345", "new") not in adapter._ui_member_ids
            assert ("12345", "peer") not in adapter.member_ids
            assert adapter.member_ids[("99999", "peer")] == 34567
        finally:
            adapter.index.close()

    asyncio.run(run())


def test_member_card_unknown_outcome_invalidates_old_bindings_without_retry():
    async def run():
        calls = []

        async def set_card(*args, **kwargs):
            calls.append((args, kwargs))
            raise MutationOutcomeUnknown("Submitted but readback unavailable")

        scope = SimpleNamespace(
            id="12345", target=Target("12345", "测试群"), set_member_card=set_card
        )
        adapter = OneBotAdapter(
            SimpleNamespace(conversation=lambda _: scope),
            23456,
            member_ids={("12345", "peer"): 34567},
        )
        adapter._ui_member_ids[("12345", "peer")] = (34567, float("inf"))
        try:
            response = await adapter.handle(
                {
                    "action": "x_set_member_card",
                    "params": {
                        "target_id": 12345,
                        "member_name": "peer",
                        "value": "new",
                        "expected_user_id": "34567",
                    },
                }
            )
            assert response["retcode"] != 0 and len(calls) == 1
            assert adapter.member_ids == adapter._ui_member_ids == {}
        finally:
            adapter.index.close()

    asyncio.run(run())


def test_member_card_conflicting_explicit_mapping_is_rejected_before_submission():
    async def run():
        scope = SimpleNamespace(id="12345", target=Target("12345", "测试群"))
        adapter = OneBotAdapter(
            SimpleNamespace(conversation=lambda _: scope),
            23456,
            member_ids={("12345", "new"): 45678},
        )
        try:
            response = await adapter.handle(
                {
                    "action": "x_set_member_card",
                    "params": {
                        "target_id": 12345,
                        "member_name": "peer",
                        "value": "new",
                        "expected_user_id": "34567",
                    },
                }
            )
            assert response["retcode"] == 1400
        finally:
            adapter.index.close()

    asyncio.run(run())
