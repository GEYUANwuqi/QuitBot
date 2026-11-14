"""Authenticated console login sessions over a dedicated desktop worker."""

import asyncio
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from .discovery import DiscoveredWindow


class QQLoginStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hwnd: int | None = Field(default=None, gt=0)
    pid: int | None = Field(default=None, gt=0)
    new_instance: StrictBool = False

    @model_validator(mode="after")
    def check_window_selection(self):
        if (self.hwnd is None) != (self.pid is None):
            raise ValueError("请选择完整的 QQ 进程窗口")
        if self.new_instance and self.hwnd is not None:
            raise ValueError("新开登录窗口时无需选择已有 QQ")
        return self


class QQLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login_id: str = Field(min_length=32, max_length=64)


class QQLoginAction(QQLoginRequest):
    action: Literal["login", "qr", "refresh_qr", "back", "open_existing"]
    account_key: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class SavedQQAccount(BaseModel):
    key: str
    account_id: str | None = None
    nickname: str
    selected: bool = False


class QQLoginView(BaseModel):
    login_id: str
    state: Literal[
        "starting",
        "accounts",
        "qr",
        "expired",
        "waiting",
        "existing",
        "logged_in",
        "closed",
    ]
    accounts: list[SavedQQAccount] = Field(default_factory=list)
    qr_image: str | None = None
    can_go_back: bool = False
    profile: DiscoveredWindow | None = None
    message: str = ""


class QQLoginManager:
    def __init__(self, driver_factory=None):
        self._factory = driver_factory
        self._driver = None
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="qq-login"
        )
        self._jobs = set()
        self._closed = False
        self._login_id = None
        self._expires = 0

    def _run(self, method, request):
        if self._driver is None:
            if self._factory is None:
                from ._engine.ui.workflows.login import QQLoginDriver

                self._factory = QQLoginDriver
            self._driver = self._factory()
        if method == "start":
            # Replace the polling token; the driver reuses a visible client or
            # invokes QQ's normal startup flow to reveal a hidden client.
            self._login_id = None
            data = self._driver.start(
                request.hwnd, request.pid, new_instance=request.new_instance
            )
            self._login_id = secrets.token_urlsafe(24)
            self._expires = time.monotonic() + 1800
        else:
            if request.login_id != self._login_id or time.monotonic() > self._expires:
                raise ValueError("登录流程已结束，请重新打开 QQ 登录")
            data = (
                self._driver.read()
                if method == "read"
                else self._driver.act(request.action, request.account_key)
            )
        return QQLoginView(login_id=self._login_id, **data)

    async def call(self, method, request):
        if self._closed:
            raise ValueError("服务正在关闭")
        if len(self._jobs) >= 8:
            raise ValueError("QQ 登录操作繁忙，请稍后读取状态")
        # Submitted actions finish even if the browser disconnects. No action replay.
        future = asyncio.wrap_future(self._executor.submit(self._run, method, request))
        self._jobs.add(future)
        future.add_done_callback(self._jobs.discard)
        try:
            return await asyncio.shield(future)
        except (OSError, RuntimeError) as error:
            raise ValueError(str(error)) from error

    async def drain(self):
        self._closed = True
        if self._jobs:
            await asyncio.gather(*tuple(self._jobs), return_exceptions=True)

    async def close(self):
        await self.drain()
        if self._driver is not None:
            await asyncio.wrap_future(self._executor.submit(self._driver.close))
        self._executor.shutdown(wait=True)
