"""Persistent HTTP credentials remain scoped to their account and transport."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from auth_helpers import authenticated
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect
from test_configuration import port

from quitbot_backend.app import create_app
from quitbot_backend.auth import AccessKeys
from quitbot_backend.config import AccountConfig, ConfigStore, ServiceConfig
from quitbot_backend.management import create_management_app
from quitbot_backend.service import Service

TOKEN = "fixture-http-credential"


def test_http_token_is_separate_from_ws_console_and_other_accounts(tmp_path):
    access = AccessKeys()
    session = authenticated(access)
    config = AccountConfig(
        account_id="10001",
        http={"access_token": TOKEN},
        forward_ws={"access_token": "fixture-ws-token"},
    )
    app = create_app(config, access=access, state_dir=tmp_path)
    other = create_app(
        AccountConfig(
            account_id="10002", http={"access_token": "fixture-other-http-token"}
        ),
        access=access,
        state_dir=tmp_path,
    )
    service = Service(ConfigStore(tmp_path / "accounts.json"), access)
    service.accounts["10001"] = SimpleNamespace(app=app, runtime=app.state.runtime)
    headers = {"Authorization": "Bearer " + TOKEN}
    session_headers = {"Authorization": "Bearer " + session}
    with (
        TestClient(app) as business,
        TestClient(other) as another,
        TestClient(create_management_app(service)) as console,
    ):
        assert business.get("/health", headers=headers).json()["account_id"] == "10001"
        reply = business.post(
            "/api/x_get_runtime_status", json={}, headers=headers
        ).json()
        assert reply["retcode"] == 0 and reply["data"]["account_id"] == "10001"
        assert TOKEN not in str(reply)
        assert (
            business.post(
                "/api/x_get_runtime_status", json={"unknown": True}, headers=headers
            ).status_code
            == 422
        )
        for invalid in (
            None,
            "Basic " + TOKEN,
            "Bearer fixture-ws-token",
            "Bearer " + session,
            "Bearer fixture-wrong-token",
        ):
            response = business.post(
                "/api/x_connect?access_token=" + TOKEN,
                json={},
                headers={"Authorization": invalid} if invalid else {},
            )
            assert (
                response.status_code == 401
                and response.headers["WWW-Authenticate"] == "Bearer"
            )
            assert TOKEN not in response.text
        assert app.state.runtime.qq is None
        assert another.get("/health", headers=headers).status_code == 401
        assert console.get("/api/config", headers=headers).status_code == 401
        assert console.get("/api/config", headers=session_headers).status_code == 200
        for client, path in (
            (business, "/ws"),
            (console, "/ws"),
            (console, "/accounts/10001/ws"),
        ):
            with client.websocket_connect(path, headers=headers) as ws:
                with pytest.raises(WebSocketDisconnect) as failure:
                    ws.receive_json()
                assert failure.value.code == 4401
        with console.websocket_connect(
            "/accounts/10001/ws", headers=session_headers
        ) as ws:
            assert ws.receive_json()["self_id"] == 10001
        changed = console.post(
            "/auth/password",
            headers=session_headers,
            json={
                "current_password": "fixture-password-only",
                "new_password": "fixture-replacement-password",
            },
        )
        assert changed.status_code == 200
        assert business.get("/health", headers=headers).status_code == 200
        assert (
            console.post(
                "/auth/logout",
                headers={"Authorization": "Bearer " + changed.json()["access_token"]},
            ).status_code
            == 200
        )
        assert business.get("/health", headers=headers).status_code == 200


def test_http_token_maximum_length_and_validation(tmp_path):
    token = "x" * 4096
    config = AccountConfig(account_id="10001", http={"access_token": token})
    with TestClient(
        create_app(config, access=AccessKeys(), state_dir=tmp_path)
    ) as client:
        assert (
            client.get(
                "/health", headers={"Authorization": "Bearer " + token}
            ).status_code
            == 200
        )
    assert token not in repr(config)
    for invalid in ("with space", "with\nnewline", "中文", "x" * 4097, 123):
        with pytest.raises(ValidationError):
            AccountConfig(account_id="10001", http={"access_token": invalid})


def test_http_token_survives_service_restart_and_can_rotate_or_return_to_session_auth(
    tmp_path,
):
    async def run():
        address = port()
        path = tmp_path / "accounts.json"
        token = TOKEN
        for cycle in range(2):
            access = AccessKeys()
            session = authenticated(access)
            service = Service(ConfigStore(path), access)
            try:
                if cycle == 0:
                    config = ServiceConfig(
                        accounts=[
                            AccountConfig(
                                account_id="10001",
                                http={
                                    "enabled": True,
                                    "port": address,
                                    "access_token": token,
                                },
                            )
                        ]
                    )
                    assert not (await service.update(config, service.store.revision))[
                        "errors"
                    ]
                else:
                    await service.reload()
                    assert not service.errors
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{address}", trust_env=False
                ) as client:
                    assert (
                        await client.get(
                            "/health", headers={"Authorization": "Bearer " + token}
                        )
                    ).status_code == 200
                    changed = service.store.current.model_copy(deep=True)
                    changed.accounts[0].http.access_token = (
                        "fixture-rotated-http-token" if cycle == 0 else ""
                    )
                    result = await service.update(changed, service.store.revision)
                    assert (
                        not result["errors"]
                        and result["revision"] == result["applied_revision"]
                    )
                    assert (
                        await client.get(
                            "/health", headers={"Authorization": "Bearer " + token}
                        )
                    ).status_code == 401
                    token = changed.accounts[0].http.access_token
                    assert (
                        await client.get(
                            "/health",
                            headers={"Authorization": "Bearer " + (token or session)},
                        )
                    ).status_code == 200
                    if token:
                        assert (
                            await client.get(
                                "/health",
                                headers={"Authorization": "Bearer " + session},
                            )
                        ).status_code == 401
                assert ConfigStore(path).current.accounts[0].http.access_token == token
            finally:
                await service.close()

    asyncio.run(run())
