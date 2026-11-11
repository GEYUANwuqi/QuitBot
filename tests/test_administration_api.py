import asyncio
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.adapters.onebot.api import OneBotAdapter
from quitbot_backend._engine.domain.administration import MemberAdminReceipt
from quitbot_backend._engine.models import GroupMemberInfo, Target


def test_standard_admin_resolves_name_and_requires_expected_numeric_identity():
    async def run():
        calls = []

        class Scope:
            id = "12345"
            target = Target(id, "测试群")

            async def members(self):
                return [GroupMemberInfo("peer", "member")]

            async def set_member_admin(self, name, enabled, *, expected_user_id):
                calls.append((name, enabled, expected_user_id))
                return MemberAdminReceipt(
                    self.id, name, expected_user_id, not enabled, enabled
                )

        adapter = OneBotAdapter(
            SimpleNamespace(
                targets=[Target("34567", "peer", "private")],
                conversation=lambda _: Scope(),
            ),
            23456,
        )
        try:
            for enabled in (True, False):
                result = await adapter.handle(
                    {
                        "action": "set_group_admin",
                        "params": {
                            "group_id": 12345,
                            "user_id": 34567,
                            "enable": enabled,
                        },
                        "echo": "admin",
                    }
                )
                assert result["retcode"] == 0 and result["data"] is None
                assert result["echo"] == "admin"
            assert calls == [("peer", True, "34567"), ("peer", False, "34567")]
            result = await adapter.handle(
                {
                    "action": "x_set_member_admin",
                    "params": {
                        "target_id": 12345,
                        "member_name": "peer",
                        "enabled": True,
                        "expected_user_id": "34567",
                    },
                }
            )
            assert result["retcode"] == 0 and result["data"]["user_id"] == "34567"
        finally:
            adapter.index.close()

    asyncio.run(run())


@pytest.mark.parametrize("enabled", [1, "true"])
def test_standard_admin_bad_boolean_rejects_before_ui_access(enabled):
    adapter = OneBotAdapter(SimpleNamespace(), 23456)
    try:
        response = asyncio.run(
            adapter.handle(
                {
                    "action": "set_group_admin",
                    "params": {"group_id": 12345, "user_id": 34567, "enable": enabled},
                }
            )
        )
        assert response["retcode"] == 1400
    finally:
        adapter.index.close()
