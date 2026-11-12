"""Manage account listeners and apply validated configuration without exiting."""

import asyncio
import logging
import socket
import time
from contextlib import AsyncExitStack, nullcontext
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .app import create_app
from .auth import AccessKeys
from .config import AccountConfig, ConfigStore, ServiceConfig
from .discovery import (
    DiscoveredWindow,
    DiscoveryView,
    read_window_profile,
    scan_windows,
    windows_session_id,
)
from .reverse_ws import ReverseWebSocket
from .runtime import AccountRuntime

log = logging.getLogger(__name__)


@dataclass
class ListenerServer:
    server: uvicorn.Server
    task: asyncio.Task
    listener: socket.socket


@dataclass
class AccountServer:
    config: AccountConfig
    runtime: AccountRuntime
    app: FastAPI
    lifespan: AsyncExitStack
    listeners: list[ListenerServer]
    reverse_ws: ReverseWebSocket


def bound_socket(host: str, port: int) -> socket.socket:
    listener = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if ":" in host:
            listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        listener.bind((host, port))
        listener.listen(128)
        listener.setblocking(False)
        return listener
    except BaseException:
        listener.close()
        raise


def server_for(app, host: str, port: int, *, lifespan="auto") -> uvicorn.Server:
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            ws_max_size=1024 * 1024,
            ws_max_queue=16,
            log_level="warning",
            access_log=False,
            lifespan=lifespan,
        )
    )
    server.capture_signals = nullcontext
    return server


def transport_app(app, *, http: bool, ws: bool):
    """Expose only the transport enabled on this listener's address."""

    async def endpoint(scope, receive, send):
        if scope["type"] == "websocket" and not ws:
            await send({"type": "websocket.close", "code": 1008})
        elif scope["type"] == "http" and not http:
            await JSONResponse({"detail": "HTTP is disabled on this listener"}, 404)(
                scope, receive, send
            )
        else:
            await app(scope, receive, send)

    return endpoint


class Service:
    def __init__(
        self,
        store: ConfigStore,
        access: AccessKeys,
        *,
        management_port=None,
        management_host=None,
        port_overridden=False,
        runtime_factory=AccountRuntime,
        discover=scan_windows,
        resolve_profile=read_window_profile,
    ):
        self.store, self.access = store, access
        self.management_port = management_port or store.current.webui.port
        self.management_host = management_host or store.current.webui.host
        self.port_overridden = port_overridden
        self.runtime_factory = runtime_factory
        self.accounts: dict[str, AccountServer] = {}
        self.applied_revision: str | None = None
        self.errors: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._observed = None
        self._jobs = set()
        self.stopping = asyncio.Event()
        self.discovery = DiscoveryView(session_id=windows_session_id())
        self._discover = discover
        self._resolve_profile = resolve_profile
        self._discovery_task = None
        self._activated: dict[str, DiscoveredWindow] = {}
        webui = store.current.webui
        self.access.configure_login(
            webui.login_attempts, webui.login_retry_seconds, webui.session_hours
        )

    @property
    def webui_restart_required(self):
        wanted = self.store.current.webui
        return wanted.host != self.management_host or (
            not self.port_overridden and wanted.port != self.management_port
        )

    def start_discovery(self):
        if self._discovery_task is None or self._discovery_task.done():
            if self.stopping.is_set():
                raise RuntimeError("服务正在关闭")
            self.discovery = DiscoveryView(
                state="scanning", session_id=self.discovery.session_id
            )
            self._discovery_task = asyncio.create_task(self._scan())
        return self._discovery_task

    async def refresh_discovery(self) -> DiscoveryView:
        return await asyncio.shield(self.start_discovery())

    async def _scan(self) -> DiscoveryView:
        try:
            windows = await asyncio.to_thread(self._discover)
            self.discovery = DiscoveryView(
                state="ready",
                session_id=self.discovery.session_id,
                scanned_at=time.time(),
                windows=windows,
            )
            available = {(w.hwnd, w.pid) for w in windows}
            self._activated = {
                number: identity
                for number, identity in self._activated.items()
                if (identity.hwnd, identity.pid) in available
            }
        except Exception as error:
            log.exception("QQ window discovery failed")
            self.discovery = DiscoveryView(
                state="failed",
                session_id=self.discovery.session_id,
                scanned_at=time.time(),
                error=str(error),
            )
            self._activated.clear()
        return self.discovery

    async def activate_account(self, hwnd: int, revision: str) -> dict:
        task = asyncio.create_task(self._activate_account(hwnd, revision))
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)
        return await asyncio.shield(task)

    async def _activate_account(self, hwnd: int, revision: str) -> dict:
        async with self._lock:
            if self.stopping.is_set():
                raise ValueError("服务正在关闭")
            candidates = [w for w in self.discovery.windows if w.hwnd == hwnd]
            if self.discovery.state != "ready" or len(candidates) != 1:
                raise ValueError("请先探查并选择 QQ 进程，再连接账号")
            config, current_revision = self.store.read()
            if current_revision != revision:
                raise ValueError("配置已被其他窗口或文件修改，请重新读取后再连接")
            candidate = candidates[0]
            self._activated = {
                number: identity
                for number, identity in self._activated.items()
                if identity.hwnd != hwnd
            }
            try:
                window = await asyncio.to_thread(self._resolve_profile, candidate)
            except (OSError, RuntimeError) as error:
                raise ValueError(str(error)) from error
            if (window.hwnd, window.pid) != (candidate.hwnd, candidate.pid):
                raise ValueError("QQ 进程或窗口已变化，请刷新探查后重新连接")
            if not window.available:
                raise ValueError(
                    window.error
                    or "未能完整读取 QQ 号、昵称和头像，请确认窗口已登录后重试"
                )
            account = next(
                (a for a in config.accounts if a.account_id == window.account_id), None
            )
            if account is None:
                account = AccountConfig(
                    account_id=window.account_id, label=window.nickname, hwnd=hwnd
                )
                config.accounts.append(account)
            else:
                account.hwnd = hwnd
            self.validate(config)
            self.store.save(config, revision)
            await self._apply(config, self.store.revision)
            if window.account_id in self.errors:
                raise ValueError(self.errors[window.account_id])
            result = await self.accounts[window.account_id].runtime.handle(
                {"action": "x_connect"}
            )
            if result["status"] != "ok":
                raise ValueError(result.get("message", "QQ 窗口连接失败"))
            self._activated[window.account_id] = window
            return self.snapshot()

    def snapshot(self) -> dict:
        return {
            "config": self.store.current.model_dump(mode="json"),
            "revision": self.store.revision,
            "applied_revision": self.applied_revision,
            "errors": self.errors,
            "activated_accounts": [
                w.model_dump()
                for w in self._activated.values()
                if w.account_id in self.accounts
                and self.accounts[w.account_id].config.hwnd == w.hwnd
            ],
            "webui": {
                "host": self.management_host,
                "port": self.management_port,
                "url": f"http://{'[' + self.management_host + ']' if ':' in self.management_host else self.management_host}:{self.management_port}/",
                "restart_required": self.webui_restart_required,
                "port_overridden": self.port_overridden,
            },
            "accounts": [
                {
                    "account_id": number,
                    "status": item.runtime.status(),
                }
                for number, item in self.accounts.items()
            ],
        }

    def validate(self, config: ServiceConfig):
        ServiceConfig.model_validate(config.model_dump())
        if any(
            endpoint.enabled and endpoint.port == self.management_port
            for account in config.accounts
            for endpoint in (account.http, account.forward_ws)
        ):
            raise ValueError("账号端口不能与管理服务端口相同")

    async def update(self, config: ServiceConfig, revision: str) -> dict:
        task = asyncio.create_task(self._update(config, revision))
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)
        return await asyncio.shield(task)

    async def _update(self, config: ServiceConfig, revision: str) -> dict:
        async with self._lock:
            self.validate(config)
            self.store.save(config, revision)
            await self._apply(self.store.current, self.store.revision)
            return self.snapshot()

    async def reload(self):
        async with self._lock:
            try:
                config, revision = self.store.read()
                self.validate(config)
            except (OSError, ValueError) as error:
                message = (
                    str(error.errors(include_input=False, include_url=False))
                    if isinstance(error, ValidationError)
                    else str(error)
                )
                if self.errors.get("configuration") != message:
                    log.warning("Configuration rejected: %s", message)
                self.errors["configuration"] = message
                return
            if revision == self._observed:
                self.errors.pop("configuration", None)
                return
            self.store.current, self.store.revision = config, revision
            await self._apply(config, revision)

    async def _start(self, config: AccountConfig, *, bound=False, listening=False):
        runtime = self.runtime_factory(config, self.store.path.parent / "state")
        app = create_app(
            config,
            access=self.access,
            runtime=runtime,
            resume_bound=bound,
            resume_listening=listening,
            console_port=self.management_port,
        )
        lifespan = AsyncExitStack()
        listeners = []
        try:
            # One runtime lifecycle, even with two listeners or reverse WS only.
            endpoints = {}
            for name, endpoint in (("http", config.http), ("ws", config.forward_ws)):
                if endpoint.enabled:
                    endpoints.setdefault((endpoint.host, endpoint.port), set()).add(
                        name
                    )
            for (host, port), roles in endpoints.items():
                listener = bound_socket(host, port)
                server = server_for(
                    transport_app(app, http="http" in roles, ws="ws" in roles),
                    host,
                    port,
                    lifespan="off",
                )
                task = asyncio.create_task(server.serve(sockets=[listener]))
                listeners.append(ListenerServer(server, task, listener))
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("账号服务未能启动")
                    await asyncio.sleep(0.02)
            await lifespan.enter_async_context(app.router.lifespan_context(app))
            self.accounts[config.account_id] = AccountServer(
                config, runtime, app, lifespan, listeners, app.state.reverse_ws
            )
        except BaseException:
            for item in listeners:
                item.server.should_exit = True
            await asyncio.gather(
                *(item.task for item in listeners), return_exceptions=True
            )
            await lifespan.aclose()
            await runtime.close()
            for item in listeners:
                item.listener.close()
            raise

    async def _stop(self, number: str):
        item = self.accounts.pop(number)
        await item.reverse_ws.close()
        # Stop accepting jobs, then wait for already submitted UI operations.
        await item.runtime.close()
        for listener in item.listeners:
            listener.server.should_exit = True
        await asyncio.gather(*(listener.task for listener in item.listeners))
        await item.lifespan.aclose()
        for listener in item.listeners:
            listener.listener.close()

    async def _apply(self, config: ServiceConfig, revision: str):
        await asyncio.to_thread(
            self.access.configure_login,
            config.webui.login_attempts,
            config.webui.login_retry_seconds,
            config.webui.session_hours,
        )
        wanted = {a.account_id: a for a in config.accounts}
        previous = {}
        self.errors.clear()
        for number, item in tuple(self.accounts.items()):
            if number not in wanted or item.config != wanted[number]:
                previous[number] = (
                    item.config,
                    item.runtime.qq is not None,
                    item.runtime.status()["listener_state"] in ("starting", "running"),
                )
                await self._stop(number)
        for number, account in wanted.items():
            if number in self.accounts:
                continue
            old = previous.get(number)
            try:
                await self._start(
                    account,
                    bound=bool(old and old[1]),
                    listening=bool(old and old[2] and account.targets),
                )
            except (OSError, RuntimeError) as error:
                self.errors[number] = str(error)
                if old:
                    try:
                        await self._start(old[0], bound=old[1], listening=old[2])
                    except (OSError, RuntimeError) as rollback_error:
                        self.errors[number] += f"; 恢复旧服务失败: {rollback_error}"
        self._observed = revision
        if not self.errors and not self.webui_restart_required:
            self.applied_revision = revision

    async def watch(self):
        while not self.stopping.is_set():
            await self.reload()
            try:
                await asyncio.wait_for(self.stopping.wait(), 1)
            except TimeoutError:
                pass

    async def close(self):
        self.stopping.set()
        if self._discovery_task is not None:
            await asyncio.shield(self._discovery_task)
        if self._jobs:
            await asyncio.gather(*tuple(self._jobs), return_exceptions=True)
        async with self._lock:
            for number in tuple(self.accounts):
                await self._stop(number)
