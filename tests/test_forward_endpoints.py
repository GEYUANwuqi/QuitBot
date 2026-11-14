"""Independent formats and credentials over several listeners of one runtime."""

import asyncio
import json

import httpx
import pytest
from auth_helpers import authenticated
from pydantic import ValidationError
from test_configuration import port
from test_service import GROUP, FakeQQ
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from quitbot_backend.auth import AccessKeys
from quitbot_backend.config import AccountConfig, ConfigStore, ServiceConfig
from quitbot_backend.management import create_management_app
from quitbot_backend.runtime import AccountRuntime
from quitbot_backend.service import Service, bound_socket, server_for


def test_single_endpoint_migrates_without_rewriting_until_save(tmp_path):
    path = tmp_path / "accounts.json"
    legacy = {"enabled": True, "port": 16701, "access_token": "fixture-legacy-token"}
    raw = json.dumps({"accounts": [{"account_id": "10001", "forward_ws": legacy}]})
    path.write_text(raw)
    store = ConfigStore(path)
    assert path.read_text() == raw
    endpoints = store.current.accounts[0].forward_ws
    assert len(endpoints) == 1 and endpoints[0].message_format == "array"
    store.save(store.current, store.revision)
    assert json.loads(path.read_text())["accounts"][0]["forward_ws"] == [
        {**legacy, "host": "127.0.0.1", "message_format": "array"}
    ]
    assert ConfigStore(path).current == store.current
    assert AccountConfig(account_id="10001").forward_ws == []
    oldest = AccountConfig(account_id="10001", host="127.0.0.1", port=16702)
    assert oldest.http.enabled and oldest.forward_ws[0].enabled
    assert oldest.http.port == oldest.forward_ws[0].port == 16702


def test_multiple_endpoints_validate_formats_and_port_ownership():
    first = {"enabled": True, "port": 16701}
    second = {"enabled": True, "port": 16702, "message_format": "string"}
    account = AccountConfig(account_id="10001", http=first, forward_ws=[first, second])
    ServiceConfig(accounts=[account])  # HTTP may share one WS listener.
    for invalid in (
        [first, first],
        [{**first, "message_format": "xml"}],
    ):
        with pytest.raises(ValidationError):
            AccountConfig(account_id="10001", forward_ws=invalid)
    for other in (
        {"account_id": "10002", "forward_ws": [second]},
        {"account_id": "10002", "http": first},
    ):
        with pytest.raises(ValidationError):
            ServiceConfig(accounts=[account, other])
    with pytest.raises(ValidationError):
        ServiceConfig(accounts=[account], webui={"port": 16702})
    with pytest.raises(ValidationError):
        ServiceConfig(
            accounts=[
                {
                    "account_id": "10001",
                    "forward_ws": [first, {**first, "host": "0.0.0.0"}],
                }
            ]
        )
    AccountConfig(account_id="10001", forward_ws=[first, {**first, "enabled": False}])


def test_live_array_and_string_endpoints_share_runtime_and_isolate_auth(tmp_path):
    async def run():
        ports = set()
        while len(ports) < 3:
            ports.add(port())
        manager_port, array_port, string_port = ports
        access = AccessKeys()
        session = authenticated(access)
        store = ConfigStore(tmp_path / "accounts.json")
        instances = []

        async def factory(number, **_):
            qq = FakeQQ(number)
            instances.append(qq)
            return qq

        service = Service(
            store,
            access,
            management_port=manager_port,
            runtime_factory=lambda config, path: AccountRuntime(
                config, path, connect=factory
            ),
        )
        config = ServiceConfig(
            webui={"port": manager_port},
            accounts=[
                AccountConfig(
                    account_id="10001",
                    targets=[{"id": GROUP.id, "name": GROUP.name}],
                    member_bindings=[
                        {"group_id": GROUP.id, "name": "peer", "user_id": "30001"}
                    ],
                    http={
                        "enabled": True,
                        "port": array_port,
                        "access_token": "fixture-http",
                    },
                    forward_ws=[
                        {
                            "enabled": True,
                            "port": array_port,
                            "access_token": "fixture-array",
                        },
                        {
                            "enabled": True,
                            "port": string_port,
                            "access_token": "fixture-string",
                            "message_format": "string",
                        },
                    ],
                )
            ],
        )
        manager = server_for(create_management_app(service), "127.0.0.1", manager_port)
        listener = bound_socket("127.0.0.1", manager_port)
        serving = asyncio.create_task(manager.serve(sockets=[listener]))
        pending_events = {}

        async def receive(ws):
            return json.loads(await asyncio.wait_for(ws.recv(), 3))

        async def call(ws, action, params=None):
            echo = {
                "action": action,
                "message": [{"type": "text", "data": {"text": "[unchanged]"}}],
            }
            await ws.send(
                json.dumps({"action": action, "params": params or {}, "echo": echo})
            )
            while True:
                reply = await receive(ws)
                if reply.get("echo") == echo:
                    assert reply["retcode"] == 0, reply
                    return reply["data"]
                pending_events.setdefault(ws, []).append(reply)

        def socket(number, token, path="/ws"):
            return connect(
                f"ws://127.0.0.1:{number}{path}",
                additional_headers={"Authorization": "Bearer " + token},
                proxy=None,
            )

        try:
            while not manager.started:
                if serving.done():
                    await serving
                await asyncio.sleep(0.02)
            assert not (await service.update(config, store.revision))["errors"]
            runtime = service.accounts["10001"].runtime
            async with (
                socket(
                    array_port, "fixture-array", "/ws?message_format=string"
                ) as array,
                socket(
                    string_port, "fixture-string", "/ws?message_format=array"
                ) as string,
                socket(
                    manager_port, session, "/accounts/10001/ws?message_format=string"
                ) as console,
                httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{array_port}", trust_env=False
                ) as http,
            ):
                for ws in (array, string, console):
                    assert (await receive(ws))["meta_event_type"] == "lifecycle"
                await call(array, "x_connect")
                await call(array, "x_start_listener")
                await asyncio.wait_for(runtime.sources[0].ready.wait(), 3)
                events = []
                for ws in (array, string, console):
                    while True:
                        pending = pending_events.setdefault(ws, [])
                        event = pending.pop(0) if pending else await receive(ws)
                        if event.get("post_type") == "message":
                            break
                    events.append(event)
                assert events[0] == events[2]
                assert events[1] == {**events[0], "message": "hello"}
                assert len(instances) == 1
                assert [call[0] for call in instances[0].calls].count("events") == 1
                params = {"message_id": events[0]["message_id"]}
                results = await asyncio.gather(
                    *(call(ws, "get_msg", params) for ws in (array, string, console))
                )
                assert (
                    results[0]["message"]
                    == results[2]["message"]
                    == events[0]["message"]
                )
                assert results[1]["message"] == "hello"
                assert all(
                    value["message_id"] == params["message_id"] for value in results
                )
                headers = {"Authorization": "Bearer fixture-http"}
                reply = await http.post("/api/get_msg", json=params, headers=headers)
                assert reply.json()["data"]["message"] == events[0]["message"]
                for invalid in ("fixture-array", "fixture-string", session):
                    assert (
                        await http.get(
                            "/health", headers={"Authorization": "Bearer " + invalid}
                        )
                    ).status_code == 401
                # A listener's token policy cannot be selected through query params.
                for number, token in (
                    (array_port, "fixture-string"),
                    (string_port, "fixture-array"),
                    (string_port, "fixture-http"),
                    (string_port, session),
                ):
                    async with socket(
                        number, token, "/ws?access_token=&console=true"
                    ) as rejected:
                        with pytest.raises(ConnectionClosed) as closed:
                            await receive(rejected)
                        assert closed.value.rcvd.code == 4401
                # Both listeners accept either input format; only their output differs.
                for ws, message in (
                    (array, "hello"),
                    (string, [{"type": "text", "data": {"text": "hello"}}]),
                ):
                    assert (
                        await call(
                            ws,
                            "send_group_msg",
                            {"group_id": int(GROUP.id), "message": message},
                        )
                    )["message_id"] > 0
                updated = config.model_copy(deep=True)
                updated.accounts[0].forward_ws.pop(0)
                updated.accounts[0].forward_ws[0].access_token = "fixture-rotated"
                result = await service.update(updated, store.revision)
                assert (
                    not result["errors"]
                    and result["revision"] == result["applied_revision"]
                )
                for ws in (array, string, console):
                    await asyncio.wait_for(ws.wait_closed(), 3)
                    assert ws.close_code == 1012
            assert (
                ConfigStore(store.path).current.accounts[0].forward_ws
                == updated.accounts[0].forward_ws
            )
            async with socket(string_port, "fixture-rotated", "/api") as ws:
                status = await call(ws, "x_get_runtime_status")
                assert status["connected"] and status["listening"]
            # The shared HTTP port survives removal of its WS configuration.
            async with httpx.AsyncClient(trust_env=False) as http:
                assert (
                    await http.get(
                        f"http://127.0.0.1:{array_port}/health", headers=headers
                    )
                ).status_code == 200
        finally:
            await service.close()
            manager.should_exit = True
            await serving
            listener.close()

    asyncio.run(run())
