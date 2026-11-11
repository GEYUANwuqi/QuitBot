"""HTTP/OpenAPI and OneBot-shaped WebSocket endpoints over the same runtime."""

import asyncio
import json
import time
from contextlib import asynccontextmanager
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

from . import __version__
from .auth import AccessKeys, authenticate_socket, guard_session
from .config import AccountConfig
from .contracts import ACTIONS, Reply
from .reverse_ws import ReverseWebSocket
from .runtime import AccountRuntime


def create_app(
    config: AccountConfig,
    *,
    access: AccessKeys,
    state_dir=Path("config/state"),
    runtime=None,
    resume_bound=False,
    resume_listening=False,
    console_port=6699,
) -> FastAPI:
    runtime = runtime or AccountRuntime(config, Path(state_dir))
    reverse = ReverseWebSocket(runtime)

    @asynccontextmanager
    async def lifespan(app):
        if config.auto_connect or resume_bound:
            result = await runtime.handle({"action": "x_connect"})
            if result["status"] != "ok":
                runtime.last_error = result["message"]
                if resume_bound:
                    raise RuntimeError("恢复 QQ 绑定失败：" + result["message"])
            elif resume_listening:
                result = await runtime.handle({"action": "x_start_listener"})
                if result["status"] != "ok":
                    raise RuntimeError("恢复监听失败：" + result["message"])
        try:
            reverse.start()
            yield
        finally:
            await reverse.close()
            await runtime.close()

    def require_access(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Security(HTTPBearer(auto_error=False))
        ],
    ):
        if credentials is None:
            raise HTTPException(
                401,
                "Access credential required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        access.session(credentials.credentials)

    app = FastAPI(
        title="QuitBot Account API",
        version=__version__,
        lifespan=lifespan,
        description="One QQ account per service. HTTP actions and WS share validation and result types. UI observations are partial; mutations are never retried automatically.",
    )
    app.state.runtime = runtime
    app.state.reverse_ws = reverse
    origins = set(config.origins) | {
        f"http://{host}:{console_port}" for host in ("localhost", "127.0.0.1", "[::1]")
    }
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )

    def origin_allowed(origin, host):
        return (
            origin is None
            or origin in origins
            or origin in {f"http://{host}", f"https://{host}"}
        )

    @app.middleware("http")
    async def check_origin(request, call_next):
        if not origin_allowed(
            request.headers.get("origin"), request.headers.get("host")
        ):
            return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
        return await call_next(request)

    @app.get("/health", tags=["service"], dependencies=[Depends(require_access)])
    async def health() -> dict[str, str]:
        return {
            "service": "quitbot",
            "account_id": config.account_id,
            "version": __version__,
        }

    def register(action):
        async def endpoint(body):
            return await runtime.handle(
                {
                    "action": action.name,
                    "params": body.model_dump(mode="json", exclude_unset=True),
                }
            )

        endpoint.__annotations__["body"] = action.params
        app.post(
            "/api/" + action.name,
            dependencies=[Depends(require_access)],
            response_model=Reply[action.result],
            response_model_exclude_unset=True,
            operation_id=action.name,
            summary=action.summary,
            tags=["extension" if action.name.startswith("x_") else "onebot"],
            openapi_extra={
                "x-websocket-action": action.name,
                "x-mutating": action.mutating,
            },
        )(endpoint)

    for action in ACTIONS.values():
        register(action)

    async def websocket(socket: WebSocket):
        if not origin_allowed(socket.headers.get("origin"), socket.headers.get("host")):
            await socket.close(code=1008)
            return
        token = await authenticate_socket(socket, access)
        if token is None:
            return
        events = socket.url.path != "/api"
        api = socket.url.path != "/event"
        queue = runtime.subscribe() if events else None
        send_lock = asyncio.Lock()

        async def send(value):
            async with send_lock:
                if not access.valid(token):
                    await socket.close(4401, "Access credential revoked")
                    return
                await socket.send_json(value)

        async def publish_events():
            while True:
                event = await queue.get()
                if event is None:
                    await socket.close(
                        code=1012 if runtime._closing else 1013,
                        reason="Account service restarting or stopped"
                        if runtime._closing
                        else "Event queue overflow; no replay available",
                    )
                    return
                await send(event)

        if events:
            await send(
                {
                    "time": int(time.time()),
                    "self_id": int(config.account_id),
                    "post_type": "meta_event",
                    "meta_event_type": "lifecycle",
                    "sub_type": "connect",
                }
            )
        sender = asyncio.create_task(publish_events()) if events else None
        watcher = asyncio.create_task(guard_session(socket, access, lambda: token))
        try:
            while True:
                raw = await socket.receive_text()
                if not access.valid(token):
                    await socket.close(4401, "Access credential revoked")
                    break
                if len(raw.encode("utf-8")) > 1024 * 1024:
                    await socket.close(code=1009)
                    break
                if not api:
                    continue
                try:
                    request = json.loads(raw)
                except (ValueError, RecursionError):
                    await send(
                        {
                            "status": "failed",
                            "retcode": 1400,
                            "data": None,
                            "message": "Invalid JSON",
                        }
                    )
                    continue
                await send(await runtime.handle(request))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            if queue is not None:
                runtime.unsubscribe(queue)
            if sender:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)

    for path in ("/ws", "/", "/api", "/event"):
        app.websocket(path)(websocket)
    app.state.websocket = websocket

    # This describes real HTTP routes. WS uses the same request/result schemas,
    # with its envelope documented as an explicit vendor extension.
    schema = app.openapi()
    schema["x-websocket"] = {
        "url": "/ws",
        "oneAccountPerEndpoint": True,
        "authentication": {
            "header": "Authorization: Bearer <access_token>",
            "firstFrame": {
                "action": "x_authenticate",
                "params": {"access_token": "<access_token>"},
            },
            "login": "Obtain a session from the console service; change the initial password before account access",
            "durationConfiguration": "webui.session_hours",
            "defaultDurationSeconds": 14400,
            "sliding": False,
        },
        "request": {
            "action": "operationId",
            "params": "HTTP request body",
            "echo": "optional JSON value",
        },
        "response": "HTTP response body, with echo preserved when supplied",
        "events": {
            "discriminator": "post_type",
            "types": ["message", "notice", "request", "meta_event", "quitbot"],
            "delivery": "best effort, no replay; slow consumers disconnected with 1013",
        },
    }
    schema["x-websocket"]["reverse"] = {
        "configuration": "accounts[].reverse_ws",
        "roles": ["Universal", "API", "Event"],
        "headers": {
            "X-Self-ID": "configured account_id",
            "X-Client-Role": "configured role (default Universal)",
            "Authorization": "Bearer <reverse_ws.access_token> when configured",
        },
        "authentication": "Receiver validates the handshake; no x_authenticate frame",
        "reconnect": "Configured interval in seconds; no request or event replay",
    }
    runtime.catalog = {
        "version": __version__,
        "actions": [
            {
                "name": a.name,
                "summary": a.summary,
                "mutating": a.mutating,
                "added": a.added,
                "params": schema["paths"]["/api/" + a.name]["post"]["requestBody"][
                    "content"
                ]["application/json"]["schema"],
                "response": schema["paths"]["/api/" + a.name]["post"]["responses"][
                    "200"
                ]["content"]["application/json"]["schema"],
            }
            for a in ACTIONS.values()
        ],
        "schemas": schema["components"]["schemas"],
    }
    return app
