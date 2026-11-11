"""Real TCP reverse connections over simulated QQ, isolated from local config."""

import asyncio
import json
from dataclasses import asdict

import pytest
from auth_helpers import authenticated
from test_configuration import port
from test_service import GROUP, FakeQQ
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from quitbot_backend.auth import AccessKeys
from quitbot_backend.config import (
    AccountConfig,
    ConfigStore,
    ReverseWSConfig,
    ServiceConfig,
)
from quitbot_backend.runtime import AccountRuntime
from quitbot_backend.service import Service


def service_for(path):
    async def factory(number, **kwargs):
        return FakeQQ(number)

    access = AccessKeys()
    token = authenticated(access)
    return Service(
        ConfigStore(path / "accounts.json"),
        access,
        runtime_factory=lambda cfg, state: AccountRuntime(cfg, state, connect=factory),
    ), token


async def packet(socket):
    return json.loads(await asyncio.wait_for(socket.recv(), 3))


async def eventually(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.01)


def test_forward_and_reverse_share_actions_and_isolate_accounts(tmp_path):
    async def run():
        peers = asyncio.Queue()

        async def accept(socket):
            await peers.put(socket)
            await socket.wait_closed()

        service, token = service_for(tmp_path)
        async with serve(accept, "127.0.0.1", 0) as receiver:
            url = f"ws://127.0.0.1:{receiver.sockets[0].getsockname()[1]}/onebot"
            config = ServiceConfig(
                accounts=[
                    AccountConfig(
                        account_id=number,
                        port=port(),
                        targets=[asdict(GROUP)],
                        activities=True,
                        member_bindings=[
                            {"group_id": GROUP.id, "name": "peer", "user_id": "30001"}
                        ],
                        reverse_ws=ReverseWSConfig(
                            enabled=True, url=url, access_token="receiver-" + number
                        ),
                    )
                    for number in ("10001", "10002")
                ]
            )
            try:
                await service.update(config, service.store.revision)
                sockets = [await asyncio.wait_for(peers.get(), 3) for _ in range(2)]
                by_id = {s.request.headers["X-Self-ID"]: s for s in sockets}
                for number, socket in by_id.items():
                    assert socket.request.headers["X-Client-Role"] == "Universal"
                    assert (
                        socket.request.headers["Authorization"]
                        == "Bearer receiver-" + number
                    )
                    assert (await packet(socket))["self_id"] == int(number)
                first = by_id["10001"]
                await first.send("{invalid")
                assert (await packet(first))["retcode"] == 1400
                await first.send(json.dumps({"action": "x_connect", "echo": {"id": 1}}))
                assert (await packet(first))["echo"] == {"id": 1}
                await first.send(
                    json.dumps(
                        {
                            "action": "send_group_msg",
                            "params": {"group_id": "99999", "message": "hello"},
                            "echo": 2,
                        }
                    )
                )
                assert (await packet(first))["retcode"] == 1400
                await first.send(
                    json.dumps(
                        {
                            "action": "send_group_msg",
                            "params": {"group_id": GROUP.id, "message": "hello"},
                            "echo": 3,
                        }
                    )
                )
                assert (await packet(first))["data"]["message_id"] > 0
                forward_url = f"ws://127.0.0.1:{config.accounts[0].forward_ws.port}/ws"
                async with connect(
                    forward_url,
                    additional_headers={"Authorization": "Bearer " + token},
                    proxy=None,
                ) as forward:
                    await packet(forward)
                    await forward.send(
                        json.dumps({"action": "x_get_runtime_status", "echo": 4})
                    )
                    status = (await packet(forward))["data"]
                    assert (
                        status["connected"]
                        and status["reverse_ws"]["state"] == "connected"
                    )
                    await first.send(
                        json.dumps({"action": "x_start_listener", "echo": 5})
                    )
                    observed = [await packet(first) for _ in range(3)]
                    assert any(
                        p.get("echo") == 5 and p["retcode"] == 0 for p in observed
                    )
                    assert any(
                        p.get("post_type") == "message" and p["self_id"] == 10001
                        for p in observed
                    )
                    assert any(p.get("notice_type") == "group_ban" for p in observed)
                    assert {
                        p["post_type"]
                        for p in [await packet(forward), await packet(forward)]
                    } == {"message", "notice"}
                await by_id["10002"].send(
                    json.dumps({"action": "x_get_runtime_status", "echo": 6})
                )
                other = await packet(by_id["10002"])
                assert other["echo"] == 6 and not other["data"]["connected"]
                assert not service.accounts["10002"].runtime.qq
            finally:
                await service.close()
            assert all(s.close_code is not None for s in sockets)

    asyncio.run(run())


def test_reverse_auth_retry_disconnect_and_shutdown_do_not_replay_writes(
    tmp_path, caplog
):
    async def run():
        peers = asyncio.Queue()
        permit = False

        def authenticate(connection, request):
            if not permit:
                return connection.respond(401, "receiver-private-body")

        async def accept(socket):
            await peers.put(socket)
            await socket.wait_closed()

        service, _ = service_for(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async with serve(
            accept, "127.0.0.1", 0, process_request=authenticate
        ) as receiver:
            config = ServiceConfig(
                accounts=[
                    AccountConfig(
                        account_id="10001",
                        port=port(),
                        targets=[asdict(GROUP)],
                        reverse_ws=ReverseWSConfig(
                            enabled=True,
                            url=f"ws://127.0.0.1:{receiver.sockets[0].getsockname()[1]}/reverse",
                            access_token="receiver-secret",
                            reconnect_interval=0.2,
                        ),
                    )
                ]
            )
            try:
                await service.update(config, service.store.revision)
                runtime = service.accounts["10001"].runtime
                await eventually(lambda: runtime.reverse_ws.state == "reconnecting")
                assert runtime.reverse_ws.last_error == "握手失败（HTTP 401）"
                assert not runtime._subscribers and not runtime.qq
                permit = True
                old = await asyncio.wait_for(peers.get(), 3)
                await packet(old)
                await old.send(json.dumps({"action": "x_connect"}))
                await packet(old)
                qq = runtime.qq

                async def extension(name, params):
                    calls.append(params["value"])
                    entered.set()
                    await release.wait()
                    return params["value"]

                runtime._extension = extension
                request = {
                    "action": "x_set_sticker_remark",
                    "params": {
                        "target_id": GROUP.id,
                        "selector": "remark",
                        "value": "once",
                    },
                    "echo": "write",
                }
                await old.send(json.dumps(request))
                await asyncio.wait_for(entered.wait(), 3)
                await old.close()
                new = await asyncio.wait_for(peers.get(), 3)
                assert (await packet(new))["post_type"] == "meta_event"
                release.set()
                await eventually(lambda: not runtime._jobs)
                await new.send(
                    json.dumps({"action": "x_get_runtime_status", "echo": "after"})
                )
                assert (await packet(new))["echo"] == "after"
                assert calls == ["once"] and len(runtime._subscribers) == 1

                entered.clear()
                release.clear()
                request["params"]["value"] = "closing"
                await new.send(json.dumps(request))
                await asyncio.wait_for(entered.wait(), 3)
                closing = asyncio.create_task(service.close())
                await asyncio.wait_for(new.wait_closed(), 3)
                assert not closing.done() and not qq.closed
                release.set()
                await asyncio.wait_for(closing, 3)
                assert calls == ["once", "closing"] and qq.closed
                assert (
                    not runtime._subscribers and runtime.reverse_ws.state == "stopped"
                )
                assert peers.empty()
            finally:
                release.set()
                await service.close()
        assert (
            "receiver-secret" not in caplog.text
            and "receiver-private-body" not in caplog.text
        )

    asyncio.run(run())


def test_reverse_roles_and_hot_reload_close_only_changed_connection(tmp_path):
    async def run():
        peers = asyncio.Queue()

        async def accept(socket):
            await peers.put(socket)
            await socket.wait_closed()

        service, _ = service_for(tmp_path)
        async with serve(accept, "127.0.0.1", 0) as receiver:
            url = f"ws://127.0.0.1:{receiver.sockets[0].getsockname()[1]}"
            config = ServiceConfig(
                accounts=[
                    AccountConfig(
                        account_id=number,
                        port=port(),
                        reverse_ws=ReverseWSConfig(
                            enabled=True,
                            url=url + "/old",
                            role=role,
                            reconnect_interval=0.2,
                        ),
                    )
                    for number, role in (("10001", "API"), ("10002", "Event"))
                ]
            )
            try:
                await service.update(config, service.store.revision)
                sockets = [await asyncio.wait_for(peers.get(), 3) for _ in range(2)]
                by_role = {s.request.headers["X-Client-Role"]: s for s in sockets}
                api, events = by_role["API"], by_role["Event"]
                assert (await packet(events))["self_id"] == 10002
                await api.send(
                    json.dumps({"action": "x_get_runtime_status", "echo": "api-only"})
                )
                assert (await packet(api))["echo"] == "api-only"
                assert not service.accounts["10001"].runtime._subscribers
                second = service.accounts["10002"]
                await events.send(
                    json.dumps({"action": "x_connect", "echo": "ignored"})
                )
                second.runtime.publish(second.runtime.event("probe"))
                assert (await packet(events))["detail_type"] == "probe"
                assert not second.runtime.qq
                config = config.model_copy(deep=True)
                config.accounts[0].reverse_ws.url = url + "/new"
                config.accounts[0].reverse_ws.role = "Universal"
                await service.update(config, service.store.revision)
                await asyncio.wait_for(api.wait_closed(), 3)
                new = await asyncio.wait_for(peers.get(), 3)
                assert (
                    new.request.path == "/new"
                    and (await packet(new))["self_id"] == 10001
                )
                assert service.accounts["10002"] is second and events.close_code is None
                config.accounts[0].reverse_ws.enabled = False
                await service.update(config, service.store.revision)
                await asyncio.wait_for(new.wait_closed(), 3)
                assert service.accounts["10001"].runtime.reverse_ws.state == "disabled"
                assert peers.empty()
                for index in range(129):
                    second.runtime.publish(
                        second.runtime.event("overflow", index=index)
                    )
                await asyncio.wait_for(events.wait_closed(), 3)
                assert events.close_code == 1013
                recovered = await asyncio.wait_for(peers.get(), 3)
                assert (await packet(recovered))["self_id"] == 10002
                second.runtime.publish(second.runtime.event("fresh"))
                assert (await packet(recovered))["detail_type"] == "fresh"
            finally:
                await service.close()
            assert events.close_code is not None

    asyncio.run(run())


@pytest.mark.parametrize(
    "value",
    [
        {"enabled": True},
        {"url": "https://example.invalid/ws"},
        {"url": "ws://user:secret@example.invalid/ws"},
        {"url": "ws://example.invalid/ws?access_token=secret"},
        {"url": "ws://example.invalid/#secret"},
        {"access_token": "secret\r\nX-Forged: value"},
        {"reconnect_interval": float("nan")},
    ],
)
def test_invalid_reverse_settings_rejected_before_network(value):
    with pytest.raises(ValueError):
        ReverseWSConfig(**value)
