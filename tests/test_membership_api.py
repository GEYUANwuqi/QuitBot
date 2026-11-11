import asyncio
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.adapters.onebot.api import OneBotAdapter
from quitbot_backend._engine.domain.controls import (
    GroupLeaveReceipt,
    MemberRemovalReceipt,
)
from quitbot_backend._engine.models import Target


def test_standard_kick_binds_loaded_name_and_passes_expected_numeric_identity():
    calls = []

    class Scope:
        id = "20001"
        target = Target(id, "测试群")

        async def members(self):
            return [SimpleNamespace(name="peer")]

        async def kick_member(self, name, *, expected_user_id):
            calls.append((name, expected_user_id))
            return MemberRemovalReceipt(self.id, name, expected_user_id, True)

    qq = SimpleNamespace(
        targets=[Target("30001", "peer", "private")],
        conversation=lambda _: Scope(),
    )
    adapter = OneBotAdapter(qq, 10001)
    try:
        response = asyncio.run(
            adapter.handle(
                {
                    "action": "set_group_kick",
                    "params": {"group_id": 20001, "user_id": 30001},
                    "echo": "kick",
                }
            )
        )
        assert response["retcode"] == 0 and response["echo"] == "kick"
        assert calls == [("peer", "30001")]
    finally:
        adapter.index.close()


@pytest.mark.parametrize("reject", [True, 1, "false"])
def test_unsupported_kick_policy_never_touches_the_ui(reject):
    adapter = OneBotAdapter(SimpleNamespace(), 10001)
    try:
        response = asyncio.run(
            adapter.handle(
                {
                    "action": "set_group_kick",
                    "params": {
                        "group_id": 20001,
                        "user_id": 30001,
                        "reject_add_request": reject,
                    },
                }
            )
        )
        assert response["retcode"] in (1400, 1404)
    finally:
        adapter.index.close()


def test_leave_receipt_does_not_claim_membership_verification_or_dissolution_support():
    assert GroupLeaveReceipt("20001", True).verified_in_ui is False
    adapter = OneBotAdapter(SimpleNamespace(), 10001)
    try:
        response = asyncio.run(
            adapter.handle(
                {
                    "action": "set_group_leave",
                    "params": {"group_id": 20001, "is_dismiss": True},
                }
            )
        )
        assert response["retcode"] == 1404
    finally:
        adapter.index.close()
