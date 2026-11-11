import asyncio
from types import SimpleNamespace

from quitbot_backend._engine.adapters.onebot.api import OneBotAdapter
from quitbot_backend._engine.models import HistoryRecord


def test_onebot_history_explicit_category_and_default():
    async def run():
        calls = []

        async def history(**kwargs):
            calls.append(kwargs)
            return [
                HistoryRecord("12345", None, "file.txt", None, "unknown", kind="file")
            ]

        adapter = OneBotAdapter(
            SimpleNamespace(conversation=lambda _: SimpleNamespace(history=history)),
            23456,
        )
        try:
            for category in ("files", None):
                params = {"target_id": 12345}
                if category is not None:
                    params["category"] = category
                response = await adapter.handle(
                    {"action": "x_get_history", "params": params}
                )
                assert response["retcode"] == 0
                assert response["data"][0]["kind"] == "file"
            assert [call["category"] for call in calls] == ["files", "all"]
        finally:
            adapter.index.close()

    asyncio.run(run())
