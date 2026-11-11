"""Discovery activation and independent listeners over a real local service."""

import asyncio
import json

import httpx
import pytest
from auth_helpers import authenticated
from test_configuration import port
from test_service import FakeQQ
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from quitbot_backend.auth import AccessKeys
from quitbot_backend.config import ConfigStore
from quitbot_backend.discovery import DiscoveredProcess, DiscoveredWindow
from quitbot_backend.management import create_management_app
from quitbot_backend.runtime import AccountRuntime
from quitbot_backend.service import Service, bound_socket, server_for


def test_activation_requires_complete_identity_and_transports_remain_independent(
    tmp_path,
):
    async def run():
        listener_ready = asyncio.Event()

        class BoundQQ(FakeQQ):
            @property
            def verified_id(self):
                return self.number

            async def identify(self):
                raise AssertionError("Connecting already verified the identity")

            async def events(self, targets, **options):
                await listener_ready.wait()
                async for event in super().events(targets, **options):
                    yield event

        scans, profile_reads = [], []
        windows = [
            DiscoveredProcess(hwnd=101, pid=201),
            DiscoveredProcess(hwnd=102, pid=202),
        ]
        profiles = [
            DiscoveredWindow(
                hwnd=101,
                pid=201,
                nickname="fixture A",
                account_id="10001",
                avatar="data:image/png;base64,fixture",
            ),
            DiscoveredWindow(
                hwnd=102, pid=202, nickname="fixture B", account_id="10002"
            ),
        ]

        def discover():
            scans.append(True)
            return windows

        def resolve_profile(window):
            profile_reads.append(window.hwnd)
            return next(profile for profile in profiles if profile.hwnd == window.hwnd)

        async def factory(number, **_):
            return BoundQQ(number)

        access = AccessKeys()
        token = authenticated(access)
        listener = bound_socket("127.0.0.1", port())
        manager_port = listener.getsockname()[1]
        service = Service(
            ConfigStore(tmp_path / "accounts.json"),
            access,
            management_port=manager_port,
            discover=discover,
            resolve_profile=resolve_profile,
            runtime_factory=lambda config, path: AccountRuntime(
                config, path, connect=factory
            ),
        )
        server = server_for(create_management_app(service), "127.0.0.1", manager_port)
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("fixture service did not start")
                await asyncio.sleep(0.02)
            await service.reload()
            assert not scans
            root = f"http://127.0.0.1:{manager_port}"
            headers = {"Authorization": "Bearer " + token}
            async with httpx.AsyncClient(
                base_url=root, headers=headers, trust_env=False
            ) as http:
                found = (await http.post("/api/discovery/refresh")).json()
                assert found["windows"] == [window.model_dump() for window in windows]
                assert not profile_reads
                stale = await http.post(
                    "/api/accounts/activate", json={"hwnd": 101, "revision": "0" * 64}
                )
                assert stale.status_code == 409 and not profile_reads
                rejected = await http.post(
                    "/api/accounts/activate",
                    json={"hwnd": 102, "revision": service.store.revision},
                )
                assert rejected.status_code == 409 and not service.accounts
                assert profile_reads == [102] and not service.store.current.accounts
                activated = (
                    await http.post(
                        "/api/accounts/activate",
                        json={"hwnd": 101, "revision": service.store.revision},
                    )
                ).json()
                assert [a["account_id"] for a in activated["activated_accounts"]] == [
                    "10001"
                ]
                assert profile_reads == [102, 101]
                assert not service.accounts["10001"].listeners
                control_url = f"ws://127.0.0.1:{manager_port}/accounts/10001/ws"
                async with connect(
                    control_url, additional_headers=headers, proxy=None
                ) as control:
                    assert json.loads(await control.recv())["self_id"] == 10001
                    await control.send(json.dumps({"action": "x_get_runtime_status"}))
                    assert json.loads(await control.recv())["data"]["connected"]
                    http_port, ws_port = port(), port()
                    while ws_port == http_port:
                        ws_port = port()
                    config = activated["config"]
                    config["accounts"][0]["http"] = {
                        "enabled": True,
                        "host": "localhost",
                        "port": http_port,
                    }
                    config["accounts"][0]["forward_ws"] = {
                        "enabled": True,
                        "host": "127.0.0.1",
                        "port": ws_port,
                    }
                    applied = (
                        await http.put(
                            "/api/config",
                            json={"config": config, "revision": activated["revision"]},
                        )
                    ).json()
                    assert not applied["errors"]
                    await asyncio.wait_for(control.wait_closed(), 3)
                    assert control.close_code == 1012
                async with httpx.AsyncClient(
                    headers=headers, trust_env=False
                ) as client:
                    assert (
                        await client.get(f"http://127.0.0.1:{http_port}/health")
                    ).status_code == 200
                    assert (
                        await client.get(f"http://127.0.0.1:{ws_port}/health")
                    ).status_code == 404
                    with pytest.raises(InvalidStatus):
                        async with connect(
                            f"ws://127.0.0.1:{http_port}/ws",
                            additional_headers=headers,
                            proxy=None,
                        ):
                            pass
                    async with connect(
                        f"ws://127.0.0.1:{ws_port}/ws",
                        additional_headers=headers,
                        proxy=None,
                    ) as forward:
                        await forward.recv()
                        await forward.send(
                            json.dumps({"action": "x_get_runtime_status"})
                        )
                        assert (
                            json.loads(await forward.recv())["data"]["account_id"]
                            == "10001"
                        )
                        config["accounts"][0]["forward_ws"]["enabled"] = False
                        disabled = (
                            await http.put(
                                "/api/config",
                                json={
                                    "config": config,
                                    "revision": applied["revision"],
                                },
                            )
                        ).json()
                        assert not disabled["errors"]
                        await asyncio.wait_for(forward.wait_closed(), 3)
                    assert (
                        await client.get(f"http://127.0.0.1:{http_port}/health")
                    ).status_code == 200
                async with connect(
                    control_url, additional_headers=headers, proxy=None
                ) as control:
                    await control.recv()
                    await control.send(json.dumps({"action": "x_get_runtime_status"}))
                    assert json.loads(await control.recv())["data"]["connected"]
                runtime = service.accounts["10001"].runtime
                empty = await runtime.handle({"action": "x_start_listener"})
                assert empty["status"] == "failed" and "监听范围" in empty["message"]
                config["accounts"][0]["targets"] = [
                    {"id": "20001", "name": "test", "kind": "group"}
                ]
                configured = (
                    await http.put(
                        "/api/config",
                        json={"config": config, "revision": disabled["revision"]},
                    )
                ).json()
                assert (
                    not configured["errors"]
                    and configured["accounts"][0]["status"]["connected"]
                )
                runtime = service.accounts["10001"].runtime
                started = await runtime.handle({"action": "x_start_listener"})
                assert started["data"]["listener_state"] == "starting"
                config["accounts"][0]["interval"] = 2
                resumed = (
                    await http.put(
                        "/api/config",
                        json={"config": config, "revision": configured["revision"]},
                    )
                ).json()
                assert resumed["accounts"][0]["status"]["listener_state"] == "starting"
                assert resumed["activated_accounts"] == activated["activated_accounts"]
                listener_ready.set()
                runtime = service.accounts["10001"].runtime
                await asyncio.wait_for(runtime.sources[0].ready.wait(), 3)
                assert runtime.status()["listening"]
                assert len(scans) == 1 and profile_reads == [102, 101]
                await http.post("/api/discovery/refresh")
                assert (await http.get("/api/config")).json()[
                    "activated_accounts"
                ] == activated["activated_accounts"]
                assert profile_reads == [102, 101]
                # Reusing a handle for a different process invalidates the cached identity.
                windows[0] = DiscoveredProcess(hwnd=101, pid=203)
                await http.post("/api/discovery/refresh")
                assert (await http.get("/api/config")).json()[
                    "activated_accounts"
                ] == []
                assert len(scans) == 3 and profile_reads == [102, 101]
        finally:
            await service.close()
            server.should_exit = True
            await task
            listener.close()

    asyncio.run(run())
