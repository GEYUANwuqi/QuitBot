"""Security and live reconfiguration boundaries; all QQ objects are simulated."""

import asyncio
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from auth_helpers import authenticated
from fastapi import HTTPException
from fastapi.testclient import TestClient
from openapi_spec_validator import validate
from websockets.asyncio.client import connect

from quitbot_backend.app import create_app
from quitbot_backend.auth import AccessKeys, PasswordChange
from quitbot_backend.config import AccountConfig, ConfigStore, ServiceConfig
from quitbot_backend.discovery import DiscoveredProcess
from quitbot_backend.management import create_management_app
from quitbot_backend.runtime import AccountRuntime
from quitbot_backend.service import Service


def port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_password_change_is_atomic_revokes_sessions_and_survives_restart(
    tmp_path,
):
    path = tmp_path / "security.json"
    access = AccessKeys(path)
    initial = access.initial_password
    first, second = access.login(initial), access.login(initial)
    assert first.must_change_password and second.must_change_password
    assert not access.valid(first.access_token)

    def change(session):
        try:
            return access.change_password(
                session.access_token,
                PasswordChange(new_password="fixture-password-only"),
            ).access_token
        except HTTPException:
            return None

    with ThreadPoolExecutor(2) as pool:
        tokens = [token for token in pool.map(change, (first, second)) if token]
    assert len(tokens) == 1
    token = tokens[0]
    disk = path.read_text("utf-8")
    assert (
        initial not in disk
        and token not in disk
        and "fixture-password-only" not in disk
    )
    assert not access.valid(first.access_token, allow_password_change=True)
    assert not access.valid(second.access_token, allow_password_change=True)
    assert access.valid(token)
    restored = AccessKeys(path)
    assert not restored.valid(token)
    assert not restored.login("fixture-password-only").must_change_password
    with pytest.raises(HTTPException):
        restored.login(initial)
    assert restored.initial_password is None


def test_http_and_websocket_authenticate_before_disclosing_account_data(tmp_path):
    access = AccessKeys(tmp_path / "security.json")
    scans = []

    def discover():
        scans.append(True)
        return [DiscoveredProcess(hwnd=101, pid=201)]

    service = Service(
        ConfigStore(tmp_path / "accounts.json"), access, discover=discover
    )
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<main>Local console</main>", "utf-8")
    with TestClient(create_management_app(service, frontend_dir=frontend)) as control:
        assert "Local console" in control.get("/").text
        validate(control.get("/openapi.json").json())
        assert control.get("/api/config").status_code == 401
        assert control.get("/api/discovery").status_code == 401
        credentials = {"password": access.initial_password}
        assert (
            control.post(
                "/auth/login",
                json=credentials,
                headers={"Origin": "https://invalid.example"},
            ).status_code
            == 403
        )
        with control.websocket_connect("/ws") as ws:
            ws.send_json({"action": "x_login", "params": credentials, "echo": "login"})
            result = ws.receive_json()
            token = result["data"]["access_token"]
            assert result["data"]["must_change_password"]
            assert (
                control.get(
                    "/api/config", headers={"Authorization": "Bearer " + token}
                ).status_code
                == 403
            )
            ws.send_json({"action": "x_get_config", "echo": 1})
            assert ws.receive_json()["retcode"] == 1403
            ws.send_json({"action": "x_refresh_discovery"})
            assert ws.receive_json()["retcode"] == 1403
            assert scans == []
            ws.send_json(
                {
                    "action": "x_change_password",
                    "params": {"new_password": "fixture-password-only"},
                }
            )
            changed = ws.receive_json()["data"]
            assert not changed["must_change_password"]
            assert not access.valid(token, allow_password_change=True)
            token = changed["access_token"]
            ws.send_json({"action": "x_get_config", "echo": 1})
            assert ws.receive_json()["data"]["config"]["accounts"] == []
            ws.send_json({"action": "x_refresh_discovery"})
            found = ws.receive_json()["data"]
            assert found["state"] == "ready" and found["windows"] == [
                {"hwnd": 101, "pid": 201, "process_name": "QQ.exe"}
            ]
            ws.send_json({"action": "x_get_discovery"})
            assert ws.receive_json()["data"] == found
            assert (
                control.get(
                    "/api/discovery", headers={"Authorization": "Bearer " + token}
                ).json()
                == found
            )
            assert len(scans) == 1
        assert control.post("/auth/login", json=credentials).status_code == 401
        assert control.post("/pair", json={}).status_code in (404, 405)
        assert (
            control.get(
                "/api/config", headers={"Authorization": "Bearer " + token}
            ).status_code
            == 200
        )
    app = create_app(
        AccountConfig(account_id="10001"), access=access, state_dir=tmp_path
    )
    with TestClient(app) as client:
        assert client.post("/api/x_get_runtime_status", json={}).status_code == 401
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"action": "x_get_runtime_status", "echo": 2})
            assert ws.receive_json()["retcode"] == 1401
        assert not app.state.runtime._subscribers
        with client.websocket_connect(
            "/ws", headers={"Origin": "http://127.0.0.1:6699"}
        ) as ws:
            ws.send_json(
                {
                    "action": "x_authenticate",
                    "params": {"access_token": token},
                    "echo": 3,
                }
            )
            assert ws.receive_json()["echo"] == 3
            assert ws.receive_json()["self_id"] == 10001


def test_console_uses_package_assets_after_changing_working_directory(tmp_path, monkeypatch):
    from quitbot_backend import management

    package = tmp_path / "installed/quitbot_backend"
    assets = package / "_webui"
    assets.mkdir(parents=True)
    (assets / "index.html").write_text("<main>Packaged console</main>", "utf-8")
    (assets / "app.js").write_text("console.log('fixture');", "utf-8")
    working = tmp_path / "working"
    unrelated = working / "frontend/dist"
    unrelated.mkdir(parents=True)
    (unrelated / "index.html").write_text("Unrelated console", "utf-8")
    monkeypatch.setattr(management, "__file__", str(package / "management.py"))
    monkeypatch.chdir(working)
    service = Service(ConfigStore(tmp_path / "accounts.json"), AccessKeys())
    with TestClient(create_management_app(service)) as client:
        assert client.get("/").text == "<main>Packaged console</main>"
        assert client.get("/app.js").text == "console.log('fixture');"
        assert client.get("/api/config").status_code == 401


def test_fixed_expiry_and_logout_close_idle_management_and_account_sockets(
    tmp_path, monkeypatch
):
    from test_service import live_server

    clock = [1_700_000_000.0, 100.0]
    monkeypatch.setattr(
        "quitbot_backend.auth.time",
        SimpleNamespace(
            time=lambda: clock[0],
            monotonic=lambda: clock[1],
            time_ns=time.time_ns,
        ),
    )
    access = AccessKeys()
    token = authenticated(access)
    service = Service(ConfigStore(tmp_path / "accounts.json"), access)
    control_app = create_management_app(service)
    account_app = create_app(
        AccountConfig(account_id="10001"), access=access, state_dir=tmp_path
    )
    expires = access.info(token).expires_at
    assert expires == clock[0] + 4 * 60 * 60
    clock[0] += 3600
    clock[1] += 3600
    assert access.info(token).expires_at == expires

    async def observe(control_url, account_url):
        headers = {"Authorization": "Bearer " + token}
        async with (
            connect(control_url + "/ws", additional_headers=headers) as control,
            connect(account_url + "/api", additional_headers=headers) as api,
            connect(account_url + "/event", additional_headers=headers) as events,
        ):
            assert json.loads(await events.recv())["self_id"] == 10001
            clock[0] += 3 * 3600
            clock[1] += 3 * 3600
            await asyncio.gather(
                *(
                    asyncio.wait_for(ws.wait_closed(), 3)
                    for ws in (control, api, events)
                )
            )
            assert all(ws.close_code == 4401 for ws in (control, api, events))
        with TestClient(control_app) as http:
            assert http.get("/auth/session", headers=headers).status_code == 401
        fresh = access.login("fixture-password-only").access_token
        async with connect(
            account_url + "/api",
            additional_headers={"Authorization": "Bearer " + fresh},
        ) as api:
            with TestClient(control_app) as http:
                assert (
                    http.post(
                        "/auth/logout", headers={"Authorization": "Bearer " + fresh}
                    ).status_code
                    == 200
                )
            await asyncio.wait_for(api.wait_closed(), 3)
            assert api.close_code == 4401

    with (
        live_server(control_app) as control_url,
        live_server(account_app) as account_url,
    ):
        asyncio.run(observe(control_url, account_url))
    assert not account_app.state.runtime._subscribers


def test_failed_password_write_preserves_session_and_login_attempts_are_throttled(
    tmp_path, monkeypatch
):
    access = AccessKeys(tmp_path / "security.json")
    token = authenticated(access)
    old = access.path.read_bytes()
    with pytest.raises(HTTPException, match="当前密码"):
        access.change_password(
            token, PasswordChange(new_password="another-fixture-password")
        )

    def fail(*_):
        raise OSError("fixture write failure")

    with monkeypatch.context() as patch:
        patch.setattr("quitbot_backend.auth.write_json", fail)
        with pytest.raises(OSError):
            access.change_password(
                token,
                PasswordChange(
                    current_password="fixture-password-only",
                    new_password="another-fixture-password",
                ),
            )
    assert access.path.read_bytes() == old and access.valid(token)
    for _ in range(5):
        with pytest.raises(HTTPException) as error:
            access.login("wrong-fixture-password")
        assert error.value.status_code == 401
    with pytest.raises(HTTPException) as error:
        access.login("fixture-password-only")
    assert error.value.status_code == 429


def test_hot_reload_preserves_unaffected_account_and_rejects_stale_or_invalid_config(
    tmp_path,
):
    async def run():
        store = ConfigStore(tmp_path / "accounts.json")
        access = AccessKeys()
        token = authenticated(access)
        service = Service(store, access)
        first, second = port(), port()
        while second == first:
            second = port()
        config = ServiceConfig(
            accounts=[
                AccountConfig(account_id="10001", port=first),
                AccountConfig(account_id="10002", port=second),
            ]
        )
        try:
            initial_revision = store.revision
            await service.update(config, initial_revision)
            old = service.accounts["10002"]
            with pytest.raises(ValueError, match="重新读取"):
                await service.update(config, initial_revision)
            store.path.write_text("{invalid", "utf-8")
            await service.reload()
            assert (
                service.accounts["10002"] is old and "configuration" in service.errors
            )
            first_runtime = service.accounts["10001"]
            config = config.model_copy(deep=True)
            config.accounts[0].label = "changed"
            store.path.write_text(config.model_dump_json(), "utf-8")
            await service.reload()
            assert service.accounts["10001"].config.label == "changed"
            assert service.accounts["10001"] is not first_runtime
            assert service.accounts["10002"] is old and not service.errors
            changed_runtime = service.accounts["10001"]
            webui_change = config.model_copy(deep=True)
            webui_change.webui.port = port()
            webui_change.webui.login_attempts = 2
            webui_change.webui.login_retry_seconds = 7
            webui_change.webui.session_hours = 1.5
            original_expiry = access.info(token).expires_at
            result = await service.update(webui_change, store.revision)
            assert result["webui"]["restart_required"]
            assert result["revision"] != result["applied_revision"]
            assert result["webui"]["port"] == 6699
            assert service.accounts["10001"] is changed_runtime
            assert service.accounts["10002"] is old
            assert access.info(token).expires_at == original_expiry
            fresh = access.login("fixture-password-only")
            assert fresh.expires_at - time.time() == pytest.approx(1.5 * 3600, abs=1)
            for _ in range(2):
                with pytest.raises(HTTPException) as failure:
                    access.login("wrong-fixture-password")
                assert failure.value.status_code == 401
            with pytest.raises(HTTPException) as limited:
                access.login("fixture-password-only")
            assert limited.value.status_code == 429
            restored = Service(ConfigStore(store.path), AccessKeys())
            assert restored.management_port == webui_change.webui.port
            assert not restored.webui_restart_required
            restored_login = restored.access.login(restored.access.initial_password)
            assert restored_login.expires_at - time.time() == pytest.approx(
                1.5 * 3600, abs=1
            )
            await restored.close()
            async with connect(
                f"ws://127.0.0.1:{first}/ws",
                additional_headers={"Authorization": "Bearer " + token},
            ) as ws:
                assert json.loads(await ws.recv())["self_id"] == 10001
                await ws.send(json.dumps({"action": "x_get_runtime_status", "echo": 1}))
                assert json.loads(await ws.recv())["data"]["label"] == "changed"
            config.accounts = config.accounts[:1]
            await service.update(config, store.revision)
            assert "10002" not in service.accounts and all(
                listener.task.done() for listener in old.listeners
            )
        finally:
            await service.close()

    asyncio.run(run())


def test_busy_port_rolls_back_old_listener_and_config_save_reports_failure(tmp_path):
    async def run():
        store = ConfigStore(tmp_path / "accounts.json")
        service = Service(store, AccessKeys())
        config = ServiceConfig(
            accounts=[AccountConfig(account_id="10001", port=port())]
        )
        try:
            await service.update(config, store.revision)
            old_port = config.accounts[0].http.port
            with socket.socket() as occupied:
                occupied.bind(("127.0.0.1", 0))
                occupied.listen()
                changed = config.model_copy(deep=True)
                changed.accounts[0].http.port = occupied.getsockname()[1]
                result = await service.update(changed, store.revision)
                assert result["errors"].get("10001")
                assert result["revision"] != result["applied_revision"]
                assert service.accounts["10001"].config.http.port == old_port
        finally:
            await service.close()

    asyncio.run(run())


def test_cancelled_update_waits_for_submitted_action_and_completes_once(tmp_path):
    async def run():
        class FakeQQ:
            async def identify(self):
                return "10001"

            async def close(self):
                pass

        async def factory(*args, **kwargs):
            return FakeQQ()

        service = Service(
            ConfigStore(tmp_path / "accounts.json"),
            AccessKeys(),
            runtime_factory=lambda cfg, path: AccountRuntime(
                cfg, path, connect=factory
            ),
        )
        config = ServiceConfig(
            accounts=[AccountConfig(account_id="10001", port=port())]
        )
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        try:
            await service.update(config, service.store.revision)
            runtime = service.accounts["10001"].runtime
            await runtime.handle({"action": "x_connect"})

            async def extension(name, params):
                calls.append(name)
                entered.set()
                await release.wait()
                return "remark"

            runtime._extension = extension
            action = asyncio.create_task(
                runtime.handle(
                    {
                        "action": "x_get_sticker_remark",
                        "params": {"target_id": "20001", "selector": "x"},
                    }
                )
            )
            await entered.wait()
            changed = config.model_copy(deep=True)
            changed.accounts[0].label = "after"
            update = asyncio.create_task(
                service.update(changed, service.store.revision)
            )
            await asyncio.sleep(0.05)
            assert not action.done() and not update.done()
            update.cancel()
            with pytest.raises(asyncio.CancelledError):
                await update
            release.set()
            assert (await action)["status"] == "ok"
            await asyncio.gather(*tuple(service._jobs))
            assert len(calls) == 1 and service.accounts["10001"].config.label == "after"
            assert service.accounts["10001"].runtime.qq is not None

            async def failed_binding(*args, **kwargs):
                raise RuntimeError("fixture binding failed")

            service.runtime_factory = lambda cfg, path: AccountRuntime(
                cfg, path, connect=failed_binding if cfg.label == "broken" else factory
            )
            broken = service.store.current.model_copy(deep=True)
            broken.accounts[0].label = "broken"
            rejected = await service.update(broken, service.store.revision)
            assert "fixture binding failed" in rejected["errors"]["10001"]
            assert rejected["revision"] != rejected["applied_revision"]
            assert service.accounts["10001"].config.label == "after"
            assert service.accounts["10001"].runtime.qq is not None
        finally:
            release.set()
            await service.close()

    asyncio.run(run())
