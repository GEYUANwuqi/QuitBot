"""Transport and lifecycle regressions. These never connect to a real QQ window."""

import asyncio
import json
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict
from types import SimpleNamespace

import pytest
import uvicorn
from auth_helpers import authenticated
from fastapi.testclient import TestClient
from openapi_spec_validator import validate
from websockets.asyncio.client import connect

from quitbot_backend._engine.domain.group_files import (
    GroupFileDownloadReceipt,
    GroupFileEntry,
    GroupFileMutationReceipt,
)
from quitbot_backend._engine.domain.stickers import FavoriteSticker
from quitbot_backend._engine.errors import MutationOutcomeUnknown
from quitbot_backend._engine.models import (
    ConversationCandidate,
    ConversationInfo,
    Message,
    SendReceipt,
    Target,
)
from quitbot_backend.app import create_app
from quitbot_backend.auth import AccessKeys
from quitbot_backend.config import AccountConfig, ServiceConfig
from quitbot_backend.contracts import ACTIONS
from quitbot_backend.runtime import AccountRuntime

GROUP = Target("20001", "test")
FILE = GroupFileEntry("a.txt", "file", size_text="4B")
FOLDER = GroupFileEntry("folder", "folder", count=2)
MESSAGE = Message("ui-row", GROUP.id, "peer", "hello", "incoming")
STICKER = FavoriteSticker("test", 1, (1, 2), "remark", 0)


class FakeQQ:
    account = "test"

    def __init__(self, number="10001"):
        self.number = number
        self.targets = (GROUP,)
        self.calls = []
        self.closed = False

    async def identify(self):
        return self.number

    async def close(self):
        self.closed = True

    async def discover_conversations(self):
        self.calls.append(("discover_conversations",))
        return [
            ConversationCandidate("fixture peer"),
            ConversationCandidate("same", False, "同名"),
        ]

    async def resolve_conversation(self, name):
        self.calls.append(("resolve_conversation", name))
        return Target("30001", name, "private")

    def conversation(self, key):
        if str(key) != GROUP.id:
            raise ValueError("Unconfigured target")
        return FakeScope(self)

    async def events(self, targets, **options):
        assert targets == (GROUP.id,)
        self.calls.append(("events", options["refresh_detached"]))
        options["on_ready"]()
        yield SimpleNamespace(conversation=FakeScope(self), message=MESSAGE)
        await asyncio.Event().wait()

    async def activities(self, targets, **options):
        assert targets == (GROUP.id,)
        self.calls.append(("activities", options["refresh_detached"]))
        options["on_ready"]()
        yield {
            "kind": "system_message",
            "target": GROUP,
            "message": Message(
                "notice", GROUP.id, None, "peer被你解除禁言", "system", kind="system"
            ),
        }
        await asyncio.Event().wait()


class FakeScope:
    target = GROUP
    id = GROUP.id
    name = GROUP.name

    def __init__(self, owner):
        self.owner = owner

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            self.owner.calls.append((name, args, kwargs))
            return {
                "delete_files": [
                    GroupFileMutationReceipt(GROUP.id, "delete", FILE.name, FILE, None)
                ],
                "download_files": [
                    GroupFileDownloadReceipt(
                        GROUP.id, FILE.name, "C:/test/a.txt", 4, "abcd"
                    )
                ],
                "download_folder": [
                    GroupFileDownloadReceipt(
                        GROUP.id, FILE.name, "C:/test/a.txt", 4, "abcd"
                    )
                ],
                "folder_properties": {
                    "group_id": GROUP.id,
                    "entry": FOLDER,
                    "fields": ["count: 2"],
                },
                "save_qr": {
                    "group_id": GROUP.id,
                    "path": "C:/test/qr.png",
                    "size_bytes": 4,
                    "sha256": "abcd",
                },
                "share_qr_to": MESSAGE,
                "share_to": MESSAGE,
                "profile": ConversationInfo("30001", "peer", "private", ("QQ 30001",)),
                "like": {
                    "user_id": "30001",
                    "before_count_text": "3",
                    "after_count_text": "4",
                    "submitted": True,
                },
                "contact_profile": ConversationInfo("30001", "peer", "private"),
                "favorite_stickers": [STICKER],
                "import_stickers": [STICKER],
                "move_sticker_first": STICKER,
                "sticker_remark": "remark",
                "set_sticker_remark": "new",
                "delete_sticker": STICKER,
                "history_calendar": {
                    "conversation_id": GROUP.id,
                    "month": "2026-11",
                    "days": [{"label": "day", "enabled": True}],
                },
                "locate_history_image": MESSAGE,
                "messages": [MESSAGE],
                "get_message": MESSAGE,
                "send_text": SendReceipt(GROUP.id, "sent", "hello"),
            }[name]

        return call


def setup_app(tmp_path, **config):
    account = AccountConfig(account_id="10001", targets=[asdict(GROUP)], **config)
    qq = FakeQQ()

    async def factory(number, *, hwnd, targets):
        assert number == account.account_id and targets == (GROUP,)
        return qq

    runtime = AccountRuntime(account, tmp_path, connect=factory)
    access = AccessKeys()
    token = authenticated(access)
    app = create_app(account, access=access, runtime=runtime)
    app.state.test_token = token
    return app, runtime, qq


@contextmanager
def live_server(app):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", ws="websockets-sansio")
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 5
    try:
        while not server.started:
            if time.monotonic() > deadline or not thread.is_alive():
                raise RuntimeError("Test server failed to start")
            time.sleep(0.01)
        yield f"ws://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(5)
        listener.close()
        assert not thread.is_alive(), "Service did not shut down"


def test_schema_http_and_ws_share_catalog_and_reject_bad_input_before_uia(tmp_path):
    app, runtime, qq = setup_app(tmp_path)
    with TestClient(
        app, headers={"Authorization": "Bearer " + app.state.test_token}
    ) as client:
        spec = client.get("/openapi.json").json()
        validate(spec)
        assert {
            path.removeprefix("/api/")
            for path in spec["paths"]
            if path.startswith("/api/")
        } == ACTIONS.keys()
        assert not runtime.qq
        for action in ("x_get_capabilities", "x_get_capability_matrix"):
            catalog = client.post("/api/" + action, json={}).json()["data"]
            assert {item["name"] for item in catalog["actions"]} == ACTIONS.keys()
            assert set(catalog) == {"actions", "transport", "partial", "scope"}
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["self_id"] == 10001
            ws.send_json({"action": "x_connect", "echo": {"test": 1}})
            assert ws.receive_json()["data"]["connected"] is True
            ws.send_json(
                {
                    "action": "x_set_member_admin",
                    "params": {
                        "target_id": "20001",
                        "member_name": "peer",
                        "expected_user_id": "30001",
                        "enabled": "false",
                    },
                    "echo": 2,
                }
            )
            assert ws.receive_json()["retcode"] == 1400
            assert qq.calls == []
            ws.send_json({"action": "x_get_runtime_status", "echo": "typed"})
            via_ws = ws.receive_json()
            via_http = client.post("/api/x_get_runtime_status", json={}).json()
            assert via_ws["data"] == via_http["data"]
            assert via_ws["echo"] == "typed"
            assert client.post("/api/x_set_member_admin", json={}).status_code == 422
            ws.send_json({"action": "x_discover_conversations", "echo": "recent"})
            discovered = ws.receive_json()
            assert discovered["data"] == [
                {"name": "fixture peer", "selectable": True, "reason": None},
                {"name": "same", "selectable": False, "reason": "同名"},
            ]
            resolved = client.post(
                "/api/x_resolve_conversation", json={"name": "fixture peer"}
            ).json()
            assert resolved["data"] == {
                "id": "30001",
                "name": "fixture peer",
                "kind": "private",
            }
            assert runtime.targets == (GROUP,)
            assert qq.calls == [
                ("discover_conversations",),
                ("resolve_conversation", "fixture peer"),
            ]
    assert qq.closed


def test_all_new_routes_convert_guards_and_return_declared_types(tmp_path):
    app, _runtime, qq = setup_app(tmp_path)
    params = {
        "x_delete_group_files": {
            "names": ["a.txt", "b.txt"],
            "expected": {"a.txt": asdict(FILE)},
        },
        "x_download_group_files": {
            "names": ["a.txt", "b.txt"],
            "destination": "C:/new",
        },
        "x_download_group_folder": {
            "name": "folder",
            "download_root": "C:/downloads",
            "expected": asdict(FOLDER),
        },
        "x_get_group_folder_properties": {"name": "folder", "expected": asdict(FOLDER)},
        "x_save_group_qr": {"destination": "C:/qr.png"},
        "x_share_group_qr": {"destination": "20001"},
        "x_share_contact": {"destination": "20001"},
        "x_get_friend_profile": {"full": True},
        "x_like_friend": {},
        "x_get_contact_card_profile": {
            "ui_message_id": "ui-row",
            "expected_user_id": "30001",
        },
        "x_get_favorite_stickers": {},
        "x_import_stickers": {"paths": ["C:/test.png"]},
        "x_move_sticker_first": {"selector": "remark"},
        "x_get_sticker_remark": {"selector": "remark"},
        "x_set_sticker_remark": {"selector": "remark", "value": "new"},
        "x_delete_sticker": {"selector": "remark"},
        "x_get_history_calendar": {"year": 2026, "month": 11},
        "x_locate_history_image": {
            "on_date": "2026-11-10",
            "expected_message_id": "ui-row",
        },
        "x_get_messages": {},
    }
    assert params.keys() == {a.name for a in ACTIONS.values() if a.added}
    with TestClient(
        app, headers={"Authorization": "Bearer " + app.state.test_token}
    ) as client:
        client.post("/api/x_connect", json={}).raise_for_status()
        for name, body in params.items():
            reply = client.post(
                "/api/" + name, json={"target_id": "20001", **body}
            ).json()
            assert reply["status"] == "ok", (name, reply)
        assert isinstance(qq.calls[0][2]["expected"]["a.txt"], GroupFileEntry)
        observed = reply["data"][0]
        assert observed["message_id"] > 0 and observed["message"]["id"] == "ui-row"
        queried = client.post(
            "/api/x_get_ui_message",
            json={"target_id": "20001", "message_id": observed["message_id"]},
        ).json()
        assert queried["data"]["id"] == "ui-row"


def test_identity_mismatch_and_duplicate_account_ports_are_rejected(tmp_path):
    app, runtime, qq = setup_app(tmp_path)
    qq.number = "99999"
    with TestClient(
        app, headers={"Authorization": "Bearer " + app.state.test_token}
    ) as client:
        reply = client.post("/api/x_connect", json={}).json()
        assert reply["status"] == "failed" and not runtime.qq and qq.closed
        assert qq.calls == []
    with pytest.raises(ValueError, match="不同端口"):
        ServiceConfig(
            accounts=[
                AccountConfig(account_id="10001", http={"enabled": True, "port": 6700}),
                AccountConfig(
                    account_id="10002", forward_ws={"enabled": True, "port": 6700}
                ),
            ]
        )


def test_unknown_result_not_retried_and_cancellation_keeps_action_atomic(tmp_path):
    async def run():
        _, runtime, qq = setup_app(tmp_path)
        await runtime.handle({"action": "x_connect"})
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def extension(name, params):
            calls.append(name)
            entered.set()
            await release.wait()
            raise MutationOutcomeUnknown("submitted")

        runtime._extension = extension
        task = asyncio.create_task(
            runtime.handle(
                {
                    "action": "x_delete_sticker",
                    "params": {"target_id": "20001", "selector": "remark"},
                }
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        closing = asyncio.create_task(runtime.close())
        await asyncio.sleep(0)
        assert not qq.closed
        release.set()
        await closing
        assert len(calls) == 1 and qq.closed

    asyncio.run(run())


def test_real_websocket_multiplexes_responses_and_typed_events(tmp_path):
    app, _runtime, qq = setup_app(
        tmp_path,
        activities=True,
        refresh_detached=True,
        member_bindings=[{"group_id": GROUP.id, "name": "peer", "user_id": "30001"}],
    )
    with live_server(app) as url:

        async def run():
            async with connect(
                url + "/ws",
                additional_headers={"Authorization": "Bearer " + app.state.test_token},
            ) as ws:
                await ws.recv()
                await ws.send(json.dumps({"action": "x_connect", "echo": 1}))
                assert json.loads(await ws.recv())["data"]["connected"]
                await ws.send(json.dumps({"action": "x_start_listener", "echo": 2}))
                packets = [
                    json.loads(await asyncio.wait_for(ws.recv(), 2)) for _ in range(3)
                ]
                assert any(p.get("echo") == 2 and p["retcode"] == 0 for p in packets)
                assert any(
                    p.get("post_type") == "message" and p["user_id"] == 30001
                    for p in packets
                )
                assert any(
                    p.get("notice_type") == "group_ban" and p["duration"] == 0
                    for p in packets
                )
                await ws.send(json.dumps({"action": "x_stop_listener", "echo": 3}))
                assert (
                    json.loads(await ws.recv())["data"]["listener_state"] == "stopped"
                )

        asyncio.run(run())
    assert ("events", True) in qq.calls and ("activities", True) in qq.calls
    assert qq.closed
