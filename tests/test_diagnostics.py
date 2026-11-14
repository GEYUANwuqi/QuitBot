"""File rotation and diagnostics for failures returned through public transports."""

import asyncio
import logging
import os
import subprocess
import sys
import threading
from datetime import date, timedelta

import pytest
from auth_helpers import authenticated
from fastapi.testclient import TestClient

from quitbot_backend import diagnostics
from quitbot_backend._engine.errors import MutationOutcomeUnknown
from quitbot_backend.auth import AccessKeys
from quitbot_backend.config import AccountConfig, ConfigStore, ServiceConfig
from quitbot_backend.diagnostics import DailyLogHandler
from quitbot_backend.management import create_management_app
from quitbot_backend.service import Service


@pytest.fixture
def file_logs(tmp_path):
    handler = DailyLogHandler(tmp_path / "logs", retention_days=7)
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)
        handler.close()


def log_text(handler):
    handler.flush()
    return "\n".join(
        path.read_text("utf-8") for path in handler.directory.glob("*.log")
    )


def test_rotation_uses_calendar_days_and_preserves_saved_policy_and_other_files(
    tmp_path, monkeypatch
):
    clock = [date(2026, 1, 15)]

    class ClockDate(date):
        @classmethod
        def today(cls):
            return clock[0]

    monkeypatch.setattr(diagnostics, "date", ClockDate)
    for days in (6, 7, 14):
        (tmp_path / f"quitbot-{clock[0] - timedelta(days=days)}.log").write_text("old")
    unrelated = tmp_path / "other.log"
    unrelated.write_text("keep")
    malformed = tmp_path / "quitbot-9999-99-99.log"
    malformed.write_text("keep")
    handler = DailyLogHandler(tmp_path)
    try:
        # Startup must read a user's longer policy before cleaning anything.
        assert (tmp_path / "quitbot-2026-01-01.log").exists()
        handler.configure(30)
        assert (tmp_path / "quitbot-2026-01-01.log").exists()
        handler.configure(7)
        assert (tmp_path / "quitbot-2026-01-09.log").exists()
        assert not (tmp_path / "quitbot-2026-01-08.log").exists()
        assert not (tmp_path / "quitbot-2026-01-01.log").exists()
        clock[0] += timedelta(days=1)
        handler.handle(
            logging.LogRecord(
                "fixture", logging.ERROR, __file__, 1, "new day", (), None
            )
        )
        assert "new day" in (tmp_path / "quitbot-2026-01-16.log").read_text("utf-8")
        assert not (tmp_path / "quitbot-2026-01-09.log").exists()
        assert unrelated.read_text() == malformed.read_text() == "keep"
    finally:
        handler.close()


def test_action_and_listener_failures_keep_original_stack_and_response_codes(
    tmp_path, file_logs
):
    from test_service import setup_app

    _, runtime, qq = setup_app(tmp_path)

    async def adapter_failure(action, params):
        raise OSError("synthetic adapter failure")

    async def native_failure(name, params):
        raise MutationOutcomeUnknown("synthetic native outcome")

    async def listener_failure(targets, **options):
        raise RuntimeError("synthetic listener failure")
        yield  # pragma: no cover

    async def run():
        try:
            assert (await runtime.handle({"action": "x_connect"}))["retcode"] == 0
            runtime.adapter._action = adapter_failure
            response = await runtime.handle({"action": "get_login_info"})
            assert response["retcode"] == 100
            runtime._extension = native_failure
            response = await runtime.handle(
                {"action": "x_get_messages", "params": {"target_id": "20001"}}
            )
            assert (
                response["retcode"] == 101 and response["data"]["outcome"] == "unknown"
            )
            qq.events = listener_failure
            await runtime.handle({"action": "x_start_listener"})
            await asyncio.wait_for(runtime.sources[0].ready.wait(), 1)
            assert runtime.status()["listener_state"] == "failed"
        finally:
            await runtime.close()

    asyncio.run(run())
    text = log_text(file_logs)
    for expected in (
        '"retcode": 0',
        '"retcode": 100',
        '"retcode": 101',
        '"outcome": "unknown"',
        "adapter_failure",
        "native_failure",
        "listener_failure",
        "listener_error",
        "Traceback (most recent call last)",
    ):
        assert expected in text


def test_http_ws_validation_auth_and_config_conflicts_are_logged_without_credentials(
    tmp_path, file_logs
):
    access = AccessKeys()
    initial = access.initial_password
    token = authenticated(access)
    service = Service(ConfigStore(tmp_path / "accounts.json"), access, logs=file_logs)
    app = create_management_app(service, frontend_dir=tmp_path / "unused")
    secret = "synthetic-private-password"

    @app.get("/fixture_failure")
    async def crash():
        raise LookupError("synthetic server failure")

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post("/auth/login", json={"password": secret}).status_code == 401
        assert (
            client.post("/auth/login", json={"new_password": secret}).status_code == 422
        )
        assert (
            client.get(
                "/api/config", headers={"Origin": "https://invalid.example"}
            ).status_code
            == 403
        )
        headers = {"Authorization": "Bearer " + token}
        config = client.get("/api/config", headers=headers).json()["config"]
        assert (
            client.put(
                "/api/config",
                headers=headers,
                json={"config": config, "revision": "0" * 64},
            ).status_code
            == 409
        )
        assert client.get("/fixture_failure").status_code == 500
        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {"action": "x_authenticate", "params": {"access_token": token}}
            )
            assert ws.receive_json()["retcode"] == 0
            ws.send_json(
                {
                    "action": "x_set_config",
                    "params": {"config": config, "revision": "0" * 64},
                }
            )
            assert ws.receive_json()["retcode"] == 1400
            ws.send_json(
                {
                    "action": "x_change_password",
                    "params": {"new_password": {"private": secret}},
                }
            )
            assert ws.receive_json()["retcode"] == 1400
    text = log_text(file_logs)
    for code in (401, 403, 409, 422, 500):
        assert f"http_status={code}" in text
    assert '"action": "x_set_config"' in text and '"retcode": 1400' in text
    assert "synthetic server failure" in text and "_update" in text
    assert all(
        value not in text for value in (initial, token, secret, "fixture-password-only")
    )


def test_retention_hot_update_keeps_running_accounts_and_logs_apply_failures(
    tmp_path, file_logs
):
    service = Service(
        ConfigStore(tmp_path / "accounts.json"), AccessKeys(), logs=file_logs
    )

    async def fail_start(*args, **kwargs):
        raise RuntimeError("synthetic configuration apply failure")

    async def run():
        try:
            config = ServiceConfig(accounts=[AccountConfig(account_id="10001")])
            await service.update(config, service.store.revision)
            original = service.accounts["10001"]
            old = (
                file_logs.directory / f"quitbot-{date.today() - timedelta(days=3)}.log"
            )
            old.write_text("expired")
            config.logging.retention_days = 2
            result = await service.update(config, service.store.revision)
            assert (
                not result["errors"]
                and result["revision"] == result["applied_revision"]
            )
            assert service.accounts["10001"] is original and not old.exists()
            assert file_logs.retention_days == 2
            assert ConfigStore(service.store.path).current.logging.retention_days == 2
            config.accounts.append(AccountConfig(account_id="10002"))
            service._start = fail_start
            result = await service.update(config, service.store.revision)
            assert "10002" in result["errors"]
        finally:
            await service.close()

    asyncio.run(run())
    assert "fail_start" in log_text(file_logs)


def test_account_http_and_websocket_receipts_share_original_failure_diagnostics(
    tmp_path, file_logs
):
    from test_service import setup_app

    app, _, _ = setup_app(tmp_path)
    token = app.state.test_token
    with TestClient(app) as client:
        response = client.post(
            "/api/get_login_info", json={}, headers={"Authorization": "Bearer " + token}
        )
        assert response.status_code == 200 and response.json()["retcode"] == 100
        with client.websocket_connect("/api") as ws:
            ws.send_json(
                {"action": "x_authenticate", "params": {"access_token": token}}
            )
            ws.receive_json()
            ws.send_text("{")
            assert ws.receive_json()["retcode"] == 1400
    text = log_text(file_logs)
    assert '"action": "get_login_info"' in text and '"retcode": 100' in text
    assert "route=/api/get_login_info http_status=200" in text
    assert '"transport": "websocket"' in text and "JSONDecodeError" in text


def test_cleanup_failure_still_writes_receipts_without_breaking_operations(
    file_logs, monkeypatch, capsys
):
    def fail_cleanup(*args, **kwargs):
        raise PermissionError("synthetic locked historical log")

    monkeypatch.setattr(file_logs, "maintain", fail_cleanup)
    diagnostics.log_reply("send_group_msg", {"status": "ok", "retcode": 0})
    assert '"retcode": 0' in log_text(file_logs)
    assert "日志写入或清理失败" in capsys.readouterr().err


def test_configured_receiver_credentials_and_bearer_tokens_are_redacted(file_logs):
    receiver = "synthetic-receiver-credential"
    session = "qb_synthetic-session-credential"
    password = "synthetic-secret-password"
    file_logs.configure(7, [receiver])
    logging.getLogger(__name__).error(
        "receiver=%s Authorization: Bearer %s password=%s", receiver, session, password
    )
    text = log_text(file_logs)
    assert all(value not in text for value in (receiver, session, password))
    assert "[REDACTED]" in text


def test_unexpected_management_failure_is_logged_before_the_socket_exits(
    tmp_path, file_logs
):
    access = AccessKeys()
    token = authenticated(access)
    service = Service(ConfigStore(tmp_path / "accounts.json"), access, logs=file_logs)
    failed = threading.Event()

    async def crash(*args):
        failed.set()
        raise RuntimeError("synthetic unexpected management failure")

    service.update = crash
    with (
        TestClient(create_management_app(service)) as client,
        client.websocket_connect("/ws") as ws,
    ):
        ws.send_json({"action": "x_authenticate", "params": {"access_token": token}})
        ws.receive_json()
        ws.send_json(
            {
                "action": "x_set_config",
                "params": {
                    "config": service.store.current.model_dump(),
                    "revision": service.store.revision,
                },
            }
        )
        assert failed.wait(2)
    text = log_text(file_logs)
    assert "Unexpected management action failure action=x_set_config" in text
    assert "synthetic unexpected management failure" in text and "Traceback" in text


def test_startup_failure_creates_project_root_log_before_config_is_loaded(tmp_path):
    external = tmp_path / "separate-config"
    external.mkdir()
    config = external / "accounts.json"
    config.write_text('{"webui": {"session_hours": "invalid"}}', "utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "quitbot_backend"],
        cwd=tmp_path,
        env={**os.environ, "QUITBOT_CONFIG": str(config)},
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 1
    logs = list((tmp_path / "logs").glob("quitbot-*.log"))
    assert len(logs) == 1 and not (external / "logs").exists()
    text = logs[0].read_text("utf-8")
    assert "Starting QuitBot version=" in text
    assert "startup or service failure" in text and "Traceback" in text
    assert "session_hours" in text


@pytest.mark.parametrize("value", [0, -1, 366, 1.5, True])
def test_invalid_retention_is_rejected(value):
    with pytest.raises(ValueError):
        ServiceConfig.model_validate({"logging": {"retention_days": value}})
