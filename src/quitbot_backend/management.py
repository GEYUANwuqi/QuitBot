"""Local console login, sessions and per-account configuration."""

import asyncio
import json
from pathlib import Path
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Security,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import __version__
from .auth import (
    LoginRequest,
    PasswordChange,
    SessionCredential,
    SessionInfo,
    authenticate_socket,
    guard_session,
)
from .config import ServiceConfig
from .contracts import RuntimeStatus
from .discovery import DiscoveredWindow, DiscoveryView
from .qq_login import QQLoginAction, QQLoginRequest, QQLoginStart, QQLoginView
from .service import Service


class ConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: ServiceConfig
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class RunningAccount(BaseModel):
    account_id: str
    status: RuntimeStatus


class AccountActivation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hwnd: int = Field(gt=0)
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_account_id: str | None = Field(default=None, pattern=r"^[1-9][0-9]*$")


class WebUIStatus(BaseModel):
    host: str
    port: int
    url: str
    restart_required: bool
    port_overridden: bool


class ConfigView(BaseModel):
    config: ServiceConfig
    revision: str
    applied_revision: str | None
    errors: dict[str, str]
    accounts: list[RunningAccount]
    activated_accounts: list[DiscoveredWindow]
    webui: WebUIStatus


def create_management_app(
    service: Service, *, frontend_dir: Path | None = None
) -> FastAPI:
    app = FastAPI(title="QuitBot Management API", version=__version__)
    origins = {
        f"http://{host}:{port}"
        for host in ("localhost", "127.0.0.1")
        for port in (5173, 4173)
    }

    def allowed(origin, host):
        return (
            origin is None
            or origin in origins
            or origin in {f"http://{host}", f"https://{host}"}
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(origins),
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @app.middleware("http")
    async def check_origin(request, call_next):
        if not allowed(request.headers.get("origin"), request.headers.get("host")):
            return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/api/qq/login/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def require_session(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Security(HTTPBearer(auto_error=False))
        ],
    ):
        if credentials is None:
            raise HTTPException(
                401,
                "请先登录",
                headers={"WWW-Authenticate": "Bearer"},
            )
        service.access.session(credentials.credentials, allow_password_change=True)
        return credentials.credentials

    def require_access(token: Annotated[str, Depends(require_session)]):
        service.access.session(token)
        return token

    @app.get("/health", tags=["service"])
    async def health() -> dict[str, str]:
        return {"service": "quitbot-management"}

    @app.post("/auth/login", tags=["authentication"])
    async def login(body: LoginRequest) -> SessionCredential:
        return await asyncio.to_thread(service.access.login, body.password)

    @app.get("/auth/session", tags=["authentication"])
    async def session(token: Annotated[str, Depends(require_session)]) -> SessionInfo:
        return service.access.info(token, allow_password_change=True)

    @app.post("/auth/password", tags=["authentication"])
    async def password(
        body: PasswordChange, token: Annotated[str, Depends(require_session)]
    ) -> SessionCredential:
        return await asyncio.to_thread(service.access.change_password, token, body)

    @app.post("/auth/logout", tags=["authentication"])
    async def logout(token: Annotated[str, Depends(require_session)]) -> None:
        service.access.logout(token)

    @app.get(
        "/api/config", dependencies=[Depends(require_access)], tags=["configuration"]
    )
    async def read_config() -> ConfigView:
        return ConfigView.model_validate(service.snapshot())

    @app.get(
        "/api/discovery", dependencies=[Depends(require_access)], tags=["discovery"]
    )
    async def read_discovery() -> DiscoveryView:
        return service.discovery

    @app.post(
        "/api/discovery/refresh",
        dependencies=[Depends(require_access)],
        tags=["discovery"],
    )
    async def refresh_discovery() -> DiscoveryView:
        return await service.refresh_discovery()

    async def qq_login_call(method, body):
        try:
            return await service.qq_login.call(method, body)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post(
        "/api/qq/login/start", dependencies=[Depends(require_access)], tags=["qq-login"]
    )
    async def start_qq_login(body: QQLoginStart) -> QQLoginView:
        return await qq_login_call("start", body)

    @app.post(
        "/api/qq/login/state", dependencies=[Depends(require_access)], tags=["qq-login"]
    )
    async def read_qq_login(body: QQLoginRequest) -> QQLoginView:
        return await qq_login_call("read", body)

    @app.post(
        "/api/qq/login/action",
        dependencies=[Depends(require_access)],
        tags=["qq-login"],
    )
    async def act_qq_login(body: QQLoginAction) -> QQLoginView:
        return await qq_login_call("act", body)

    @app.post(
        "/api/accounts/activate",
        dependencies=[Depends(require_access)],
        tags=["discovery"],
    )
    async def activate_account(body: AccountActivation) -> ConfigView:
        try:
            return ConfigView.model_validate(
                await service.activate_account(
                    body.hwnd, body.revision, body.expected_account_id
                )
            )
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.websocket("/accounts/{account_id}/ws")
    async def account_socket(socket: WebSocket, account_id: str):
        if not allowed(socket.headers.get("origin"), socket.headers.get("host")):
            await socket.close(1008)
            return
        account = service.accounts.get(account_id)
        if account is None:
            await socket.close(1008)
            return
        await account.app.state.websocket(socket)

    @app.put(
        "/api/config", dependencies=[Depends(require_access)], tags=["configuration"]
    )
    async def update_config(body: ConfigUpdate) -> ConfigView:
        try:
            return ConfigView.model_validate(
                await service.update(body.config, body.revision)
            )
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.websocket("/ws")
    async def websocket(socket: WebSocket):
        if not allowed(socket.headers.get("origin"), socket.headers.get("host")):
            await socket.close(1008)
            return
        token = await authenticate_socket(socket, service.access, allow_login=True)
        if token is None:
            return

        def watch():
            return asyncio.create_task(
                guard_session(
                    socket,
                    service.access,
                    lambda: token,
                    allow_password_change=True,
                )
            )

        watcher = watch()
        try:
            while True:
                raw = await socket.receive_text()
                if not service.access.valid(token, allow_password_change=True):
                    await socket.close(4401, "Session expired or revoked")
                    break
                if len(raw.encode("utf-8")) > 1024 * 1024:
                    await socket.close(1009)
                    break
                echo = {}
                try:
                    packet = json.loads(raw)
                    if not isinstance(packet, dict) or set(packet) - {
                        "action",
                        "params",
                        "echo",
                    }:
                        raise ValueError("Use {action, params, echo?}")
                    if "echo" in packet:
                        echo = {"echo": packet["echo"]}
                    action, params = packet.get("action"), packet.get("params", {})
                    if action == "x_get_session" and not params:
                        data = service.access.info(
                            token, allow_password_change=True
                        ).model_dump()
                    elif action == "x_change_password":
                        body = PasswordChange.model_validate(params)
                        watcher.cancel()
                        await asyncio.gather(watcher, return_exceptions=True)
                        try:
                            credential = await asyncio.to_thread(
                                service.access.change_password, token, body
                            )
                            token = credential.access_token
                            data = credential.model_dump()
                        finally:
                            watcher = watch()
                    elif action == "x_logout" and not params:
                        service.access.logout(token)
                        await socket.send_json(
                            {"status": "ok", "retcode": 0, "data": None, **echo}
                        )
                        await socket.close(1000, "Signed out")
                        break
                    elif action == "x_get_config" and not params:
                        service.access.session(token)
                        data = service.snapshot()
                    elif action == "x_get_discovery" and not params:
                        service.access.session(token)
                        data = service.discovery.model_dump()
                    elif action == "x_refresh_discovery" and not params:
                        service.access.session(token)
                        data = (await service.refresh_discovery()).model_dump()
                    elif action == "x_activate_account":
                        service.access.session(token)
                        activation = AccountActivation.model_validate(params)
                        data = await service.activate_account(
                            activation.hwnd,
                            activation.revision,
                            activation.expected_account_id,
                        )
                    elif action == "x_set_config":
                        service.access.session(token)
                        update = ConfigUpdate.model_validate(params)
                        data = await service.update(update.config, update.revision)
                    elif action in {
                        "x_start_qq_login",
                        "x_get_qq_login",
                        "x_submit_qq_login",
                    }:
                        service.access.session(token)
                        method, model = {
                            "x_start_qq_login": ("start", QQLoginStart),
                            "x_get_qq_login": ("read", QQLoginRequest),
                            "x_submit_qq_login": ("act", QQLoginAction),
                        }[action]
                        data = (
                            await qq_login_call(method, model.model_validate(params))
                        ).model_dump()
                    else:
                        raise ValueError("Unknown management action")
                    response = {"status": "ok", "retcode": 0, "data": data, **echo}
                except (ValueError, TypeError, HTTPException, RecursionError) as error:
                    message = (
                        "请求格式错误，请检查字段与类型"
                        if isinstance(error, ValidationError)
                        else error.detail
                        if isinstance(error, HTTPException)
                        else str(error)
                    )
                    response = {
                        "status": "failed",
                        "retcode": 1000 + error.status_code
                        if isinstance(error, HTTPException)
                        else 1400,
                        "data": None,
                        "message": message,
                        **echo,
                    }
                await socket.send_json(response)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)

    app.openapi()["x-websocket"] = {
        "url": "/ws",
        "authentication": [
            "x_login",
            "x_authenticate",
            "Authorization: Bearer <access_token>",
        ],
        "actions": {
            "x_login": {
                "params": "LoginRequest",
                "result": "SessionCredential",
                "firstFrame": True,
            },
            "x_get_session": {"params": {}, "result": "SessionInfo"},
            "x_change_password": {
                "params": "PasswordChange",
                "result": "SessionCredential",
            },
            "x_logout": {"params": {}, "result": "null"},
            "x_get_config": {"params": {}, "result": "ConfigView"},
            "x_get_discovery": {"params": {}, "result": "DiscoveryView"},
            "x_refresh_discovery": {"params": {}, "result": "DiscoveryView"},
            "x_activate_account": {
                "params": "AccountActivation",
                "result": "ConfigView",
            },
            "x_set_config": {"params": "ConfigUpdate", "result": "ConfigView"},
            "x_start_qq_login": {"params": "QQLoginStart", "result": "QQLoginView"},
            "x_get_qq_login": {"params": "QQLoginRequest", "result": "QQLoginView"},
            "x_submit_qq_login": {"params": "QQLoginAction", "result": "QQLoginView"},
        },
        "session": {
            "durationConfiguration": "webui.session_hours",
            "defaultDurationSeconds": 14400,
            "changesApplyTo": "new sessions",
            "sliding": False,
            "firstLoginRequiresPasswordChange": True,
        },
        "accountActions": {
            "url": "/accounts/{account_id}/ws",
            "authentication": "x_authenticate or Bearer",
            "description": "Per-account console channel; independent from external HTTP and WS listeners",
        },
    }
    directory = (
        frontend_dir
        if frontend_dir is not None
        else Path(__file__).resolve().with_name("_webui")
    )
    if directory.is_dir():
        app.mount("/", StaticFiles(directory=directory, html=True), name="console")
    return app
