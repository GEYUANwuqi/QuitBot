import asyncio
from types import SimpleNamespace

from quitbot_backend._engine.adapters.onebot.api import OneBotAdapter
from quitbot_backend._engine.domain.reactions import MessageReactions, ReactionReceipt
from quitbot_backend._engine.models import Target


def test_reaction_api_uses_scoped_index_and_rejects_private_messages():
    async def run():
        calls = []

        class Scope:
            target, id = Target("12345", "测试群"), "12345"

            async def reactions(self, ui_id):
                calls.append(("read", ui_id))
                return MessageReactions(self.id, ui_id, ())

            async def add_reaction(self, ui_id, label, **kwargs):
                calls.append(("add", ui_id, label, kwargs))
                return ReactionReceipt(self.id, ui_id, label, False, True, 0, 1)

            async def remove_own_reaction(self, ui_id, **kwargs):
                calls.append(("remove", ui_id, kwargs))
                return ReactionReceipt(
                    self.id, ui_id, None, True, False, 1, 0, action="remove_own"
                )

        scope = Scope()
        adapter = OneBotAdapter(SimpleNamespace(conversation=lambda _: scope), 23456)
        try:
            number = adapter.index.register(23456, scope.target, "ui-row")
            for action in (
                "x_get_reactions",
                "x_add_reaction",
                "x_remove_own_reaction",
            ):
                response = await adapter.handle(
                    {
                        "action": action,
                        "params": {
                            "message_id": number,
                            "label": "赞",
                            "expected_text": "fixture",
                        },
                    }
                )
                assert response["retcode"] == 0
            assert calls == [
                ("read", "ui-row"),
                ("add", "ui-row", "赞", {"expected_text": "fixture"}),
                ("remove", "ui-row", {"expected_text": "fixture"}),
            ]
            private = adapter.index.register(
                23456, Target("34567", "peer", "private"), "ui-row"
            )
            invalid = await adapter.handle(
                {
                    "action": "x_add_reaction",
                    "params": {"message_id": private, "label": "赞"},
                }
            )
            assert invalid["retcode"] == 1400 and len(calls) == 3
            unknown = await adapter.handle(
                {
                    "action": "x_add_reaction",
                    "params": {"message_id": private + 1, "label": "赞"},
                }
            )
            assert unknown["retcode"] == 1400 and len(calls) == 3
        finally:
            adapter.index.close()

    asyncio.run(run())
