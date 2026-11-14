"""Account-scoped WS credentials, console isolation and live token rotation."""

import asyncio
import json
from contextlib import AsyncExitStack
from types import SimpleNamespace

import pytest
from auth_helpers import authenticated
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect
from test_configuration import port
from test_service import GROUP, FakeQQ
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from quitbot_backend.app import create_app
from quitbot_backend.auth import AccessKeys, PasswordChange
from quitbot_backend.config import AccountConfig, ConfigStore, ServiceConfig
from quitbot_backend.management import create_management_app
from quitbot_backend.runtime import AccountRuntime
from quitbot_backend.service import Service

TOKEN = "fixture-forward-credential"


@pytest.mark.parametrize("path", ["/ws", "/", "/api", "/event"])
@pytest.mark.parametrize("header", [True, False])
def test_configured_token_authenticates_all_forward_routes(tmp_path, path, header):
    access = AccessKeys()
    account = AccountConfig(account_id="10001", forward_ws={"access_token": TOKEN})
    app = create_app(account, access=access, state_dir=tmp_path)
    headers = {"Authorization": "Bearer " + TOKEN} if header else {}
    with (
        TestClient(app) as client,
        client.websocket_connect(path, headers=headers) as ws,
    ):
        if not header:
            ws.send_json(
                {
                    "action": "x_authenticate",
                    "params": {"access_token": TOKEN},
                    "echo": 1,
                }
            )
            assert ws.receive_json() == {
                "status": "ok",
                "retcode": 0,
                "data": {"authenticated": True},
                "echo": 1,
            }
        if path != "/api":
            assert ws.receive_json()["self_id"] == 10001
        if path != "/event":
            ws.send_json({"action": "x_get_runtime_status", "echo": 2})
            reply = ws.receive_json()
            assert reply["echo"] == 2 and reply["data"]["account_id"] == "10001"
            assert TOKEN not in json.dumps(reply)
    assert not app.state.runtime._subscribers


def test_forward_credentials_cannot_authorize_console_or_http(tmp_path):
    access = AccessKeys()
    session = authenticated(access)
    app = create_app(
        AccountConfig(account_id="10001", forward_ws={"access_token": TOKEN}),
        access=access,
        state_dir=tmp_path,
    )
    service = Service(ConfigStore(tmp_path / "accounts.json"), access)
    service.accounts["10001"] = SimpleNamespace(app=app, runtime=app.state.runtime)
    with (
        TestClient(app) as business,
        TestClient(create_management_app(service)) as console,
    ):
        for client, path in ((business, "/health"), (console, "/api/config")):
            assert (
                client.get(
                    path, headers={"Authorization": "Bearer " + TOKEN}
                ).status_code
                == 401
            )
            assert (
                client.get(
                    path, headers={"Authorization": "Bearer " + session}
                ).status_code
                == 200
            )
        for path in ("/ws", "/accounts/10001/ws"):
            with console.websocket_connect(path) as ws:
                ws.send_json(
                    {"action": "x_authenticate", "params": {"access_token": TOKEN}}
                )
                assert ws.receive_json()["retcode"] == 1401
                with pytest.raises(WebSocketDisconnect) as closed:
                    ws.receive_json()
                assert closed.value.code == 4401
        with console.websocket_connect("/accounts/10001/ws") as ws:
            ws.send_json(
                {"action": "x_authenticate", "params": {"access_token": session}}
            )
            assert "expires_at" in ws.receive_json()["data"]
            assert ws.receive_json()["self_id"] == 10001
            access.logout(session)
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == 4401
        # A session and query parameters cannot override the account's token policy.
        session = access.login("fixture-password-only").access_token
        with business.websocket_connect(
            "/ws?access_token=&console=true",
            headers={"Authorization": "Bearer " + session},
        ) as ws:
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == 4401


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"access_token": "wrong"},
        {"access_token": "中文"},
        {"access_token": None},
        {"access_token": [TOKEN]},
        {"access_token": TOKEN, "extra": True},
        {"access_token": "x" * 17000},
    ],
)
def test_invalid_first_frame_does_not_subscribe_or_dispatch(tmp_path, params):
    app = create_app(
        AccountConfig(account_id="10001", forward_ws={"access_token": TOKEN}),
        access=AccessKeys(),
        state_dir=tmp_path,
    )
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "x_authenticate", "params": params})
        response = ws.receive_json()
        assert response["retcode"] == 1401 and response["data"] is None
        assert TOKEN not in json.dumps(response)
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4401
        assert not app.state.runtime._subscribers


def test_maximum_token_can_be_sent_in_json_and_invalid_config_is_rejected(tmp_path):
    token = '\\"' * 2048  # JSON escaping doubles the 4096-character credential.
    account = AccountConfig(account_id="10001", forward_ws={"access_token": token})
    app = create_app(account, access=AccessKeys(), state_dir=tmp_path)
    with TestClient(app) as client, client.websocket_connect("/api") as ws:
        ws.send_json({"action": "x_authenticate", "params": {"access_token": token}})
        assert ws.receive_json()["retcode"] == 0
    for invalid in ("with space", "with\nnewline", "中文", "x" * 4097, 123):
        with pytest.raises(ValidationError):
            AccountConfig(account_id="10001", forward_ws={"access_token": invalid})
    assert token not in repr(account)


def test_live_token_rotation_preserves_binding_and_other_accounts(tmp_path):
    async def run():
        access = AccessKeys()
        session = authenticated(access)
        store = ConfigStore(tmp_path / "accounts.json")

        async def factory(number, **_):
            return FakeQQ(number)

        service = Service(
            store,
            access,
            runtime_factory=lambda config, path: AccountRuntime(
                config, path, connect=factory
            ),
        )
        ports = set()
        while len(ports) < 2:
            ports.add(port())
        first, second = ports
        config = ServiceConfig(
            accounts=[
                AccountConfig(
                    account_id="10001",
                    targets=[{"id": GROUP.id, "name": GROUP.name}],
                    http={"enabled": True, "port": first},
                    forward_ws={"enabled": True, "port": first, "access_token": TOKEN},
                ),
                AccountConfig(
                    account_id="10002",
                    forward_ws={
                        "enabled": True,
                        "port": second,
                        "access_token": "fixture-other-token",
                    },
                ),
            ]
        )

        async def call(ws, action):
            await ws.send(json.dumps({"action": action, "echo": action}))
            while True:
                reply = json.loads(await asyncio.wait_for(ws.recv(), 3))
                if reply.get("echo") == action:
                    assert reply["retcode"] == 0
                    return reply["data"]

        async def rejected(url, token):
            async with connect(
                url, additional_headers={"Authorization": "Bearer " + token}, proxy=None
            ) as ws:
                with pytest.raises(ConnectionClosed) as closed:
                    await asyncio.wait_for(ws.recv(), 3)
                assert closed.value.rcvd.code == 4401

        try:
            assert not (await service.update(config, store.revision))["errors"]
            url = f"ws://127.0.0.1:{first}/api"
            other_url = f"ws://127.0.0.1:{second}/api"
            await rejected(other_url, TOKEN)
            async with (
                connect(
                    url,
                    additional_headers={"Authorization": "Bearer " + TOKEN},
                    proxy=None,
                ) as ws,
                connect(
                    other_url,
                    additional_headers={"Authorization": "Bearer fixture-other-token"},
                    proxy=None,
                ) as other,
            ):
                await call(ws, "x_connect")
                await call(ws, "x_start_listener")
                await asyncio.wait_for(
                    service.accounts["10001"].runtime.sources[0].ready.wait(), 3
                )
                access.logout(session)
                assert (await call(ws, "x_get_runtime_status"))["listening"]
                session = access.login("fixture-password-only").access_token
                access.change_password(
                    session,
                    PasswordChange(
                        current_password="fixture-password-only",
                        new_password="fixture-replacement-password",
                    ),
                )
                assert (await call(ws, "x_get_runtime_status"))["connected"]
                assert not access.valid(session)
                previous = service.accounts["10002"]
                updated = config.model_copy(deep=True)
                updated.accounts[0].forward_ws[
                    0
                ].access_token = "fixture-replacement-token"
                result = await service.update(updated, store.revision)
                assert (
                    not result["errors"]
                    and result["revision"] == result["applied_revision"]
                )
                await asyncio.wait_for(ws.wait_closed(), 3)
                assert ws.close_code == 1012
                assert service.accounts["10002"] is previous
                assert (await call(other, "x_get_runtime_status"))[
                    "account_id"
                ] == "10002"
            await rejected(url, TOKEN)
            async with connect(url, proxy=None) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "action": "x_authenticate",
                            "params": {"access_token": "fixture-replacement-token"},
                        }
                    )
                )
                assert json.loads(await ws.recv())["retcode"] == 0
                await asyncio.wait_for(
                    service.accounts["10001"].runtime.sources[0].ready.wait(), 3
                )
                status = await call(ws, "x_get_runtime_status")
                assert status["connected"] and status["listening"]
            assert (
                store.read()[0].accounts[0].forward_ws[0].access_token
                == "fixture-replacement-token"
            )
        finally:
            await service.close()

    asyncio.run(run())


def test_multiple_clients_share_one_account_without_replacing_each_other(tmp_path):
    async def run():
        access = AccessKeys()
        service = Service(ConfigStore(tmp_path / "accounts.json"), access)
        account = AccountConfig(
            account_id="10001",
            forward_ws={"enabled": True, "port": port(), "access_token": TOKEN},
        )
        try:
            result = await service.update(
                ServiceConfig(accounts=[account]), service.store.revision
            )
            assert not result["errors"]
            runtime = service.accounts["10001"].runtime
            root = f"ws://127.0.0.1:{account.forward_ws[0].port}"
            async with AsyncExitStack() as stack:
                clients = []
                # Both handshake styles coexist on the same listener, with
                # independent event subscriptions and API replies.
                for index, path in enumerate(("/ws", "/ws", "/", "/api", "/event")):
                    headers = (
                        {"Authorization": "Bearer " + TOKEN} if index % 2 == 0 else {}
                    )
                    ws = await stack.enter_async_context(
                        connect(root + path, additional_headers=headers, proxy=None)
                    )
                    if not headers:
                        await ws.send(
                            json.dumps(
                                {
                                    "action": "x_authenticate",
                                    "params": {"access_token": TOKEN},
                                }
                            )
                        )
                        assert json.loads(await ws.recv())["retcode"] == 0
                    if path != "/api":
                        assert json.loads(await ws.recv())["self_id"] == 10001
                    clients.append(ws)
                assert len(runtime._subscribers) == 4

                async def request(ws, echo):
                    await ws.send(
                        json.dumps({"action": "x_get_runtime_status", "echo": echo})
                    )
                    reply = json.loads(await asyncio.wait_for(ws.recv(), 3))
                    assert (
                        reply["echo"] == echo and reply["data"]["account_id"] == "10001"
                    )

                await asyncio.gather(
                    *(request(ws, index) for index, ws in enumerate(clients[:4]))
                )
                event = runtime.event("fixture", sequence=1)
                runtime.publish(event)
                events = await asyncio.gather(
                    *(
                        asyncio.wait_for(clients[index].recv(), 3)
                        for index in (0, 1, 2, 4)
                    )
                )
                assert all(json.loads(value) == event for value in events)
                # Disconnecting one client leaves the other subscribers and
                # callers active. A rejected credential also cannot evict them.
                await clients[0].close()
                invalid = await stack.enter_async_context(
                    connect(
                        root + "/ws",
                        additional_headers={
                            "Authorization": "Bearer fixture-wrong-token"
                        },
                        proxy=None,
                    )
                )
                with pytest.raises(ConnectionClosed):
                    await asyncio.wait_for(invalid.recv(), 3)
                assert invalid.close_code == 4401
                await asyncio.gather(
                    *(
                        request(clients[index], f"after-close-{index}")
                        for index in (1, 2, 3)
                    )
                )
                event = runtime.event("fixture", sequence=2)
                runtime.publish(event)
                events = await asyncio.gather(
                    *(asyncio.wait_for(clients[index].recv(), 3) for index in (1, 2, 4))
                )
                assert all(json.loads(value) == event for value in events)
                replacement = await stack.enter_async_context(
                    connect(
                        root + "/ws",
                        additional_headers={"Authorization": "Bearer " + TOKEN},
                        proxy=None,
                    )
                )
                assert (
                    json.loads(await replacement.recv())["meta_event_type"]
                    == "lifecycle"
                )
                await request(replacement, "reconnected")
        finally:
            await service.close()

    asyncio.run(run())
