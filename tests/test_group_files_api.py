import asyncio
from types import SimpleNamespace

from quitbot_backend._engine.adapters.onebot.api import OneBotAdapter
from quitbot_backend._engine.domain.group_files import (
    GroupFileEntry,
    GroupFileListing,
    GroupFileMutationReceipt,
)
from quitbot_backend._engine.models import Target


def test_onebot_file_extensions_are_group_scoped_and_keep_ui_ids_null():
    calls = []
    entry = GroupFileEntry("old.txt", "file", "47.0 B")

    class Scope:
        def __init__(self, target):
            self.target = target
            self.id = target.id

        async def file_entries(self, *, folder=None):
            return GroupFileListing(self.id, (entry,))

        async def create_folder(self, name):
            calls.append(("create", name))
            return GroupFileMutationReceipt(
                self.id,
                "create_folder",
                name,
                None,
                GroupFileEntry(name, "folder", count=0),
            )

        async def rename_file(self, name, new_name, *, expected=None):
            calls.append(("rename", name, new_name, expected))
            return GroupFileMutationReceipt(
                self.id,
                "rename_file",
                new_name,
                entry,
                GroupFileEntry(new_name, "file", "47.0 B"),
            )

    group = Scope(Target("20001", "测试群"))
    private = Scope(Target("30001", "测试好友A", "private"))
    qq = SimpleNamespace(
        conversation=lambda key: {group.id: group, private.id: private}[key]
    )

    async def run():
        adapter = OneBotAdapter(qq, 10001)
        response = await adapter.handle(
            {"action": "x_get_group_file_entries", "params": {"target_id": group.id}}
        )
        assert response["status"] == "ok" and response["data"]["items"][0]["id"] is None
        response = await adapter.handle(
            {
                "action": "x_create_group_folder",
                "params": {"target_id": private.id, "name": "folder"},
            }
        )
        assert response["status"] == "failed" and calls == []
        response = await adapter.handle(
            {
                "action": "x_rename_group_file",
                "params": {
                    "target_id": group.id,
                    "name": "old.txt",
                    "new_name": "new.txt",
                    "expected": {
                        "name": "old.txt",
                        "kind": "file",
                        "size_text": "47.0 B",
                    },
                },
            }
        )
        assert response["status"] == "ok" and calls[-1][-1] == entry

    asyncio.run(run())
