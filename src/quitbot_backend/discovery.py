"""Process discovery and explicit profile reads in the current graphical session."""

import sys
from typing import Literal

from pydantic import BaseModel, Field, computed_field


class DiscoveredProcess(BaseModel):
    hwnd: int
    pid: int
    process_name: str = "QQ.exe"


class DiscoveredWindow(BaseModel):
    hwnd: int
    pid: int
    nickname: str
    account_id: str | None = None
    avatar: str | None = None
    error: str | None = None

    @computed_field
    @property
    def available(self) -> bool:
        return bool(
            self.account_id and self.nickname.strip() and self.avatar and not self.error
        )


class DiscoveryView(BaseModel):
    state: Literal["idle", "scanning", "ready", "failed"] = "idle"
    session_id: int | None = None
    scanned_at: float | None = None
    windows: list[DiscoveredProcess] = Field(default_factory=list)
    error: str | None = None


def windows_session_id() -> int | None:
    if sys.platform != "win32":
        return None
    from ._engine.platforms.windows.discovery import windows_session_id as session_id

    return session_id()


def scan_windows() -> list[DiscoveredProcess]:
    """Read native process/window metadata without accessing QQ business controls."""
    if sys.platform == "win32":
        from ._engine.platforms.windows.discovery import scan_windows as scan
    elif sys.platform == "linux":
        from ._engine.platforms.linux.discovery import scan_windows as scan
    else:
        raise RuntimeError("QQ 窗口探查需要 Windows 或 Linux X11 会话")
    return [DiscoveredProcess(**window) for window in scan()]


def read_window_profile(window: DiscoveredProcess) -> DiscoveredWindow:
    """Resolve only the selected window, keeping COM and UI cleanup on one thread."""
    if window not in scan_windows():
        raise RuntimeError("所选 QQ 进程或窗口已变化，请刷新探查后重新连接")
    from ._engine.platforms import (
        automation,
        close_automation,
        initialize_thread,
        is_provider_error,
    )

    auto = automation()

    from ._engine.discovery import discover_windows, read_self_avatar, read_self_id
    from ._engine.locking import ui_transaction
    from ._engine.ui.backend import DesktopBackend, wait_until
    from ._engine.ui.workflows.inspection import require_unlocked

    initialized = initialize_thread()
    try:
        with ui_transaction():
            require_unlocked(auto)
            auto.ControlFromHandle(window.hwnd).SetActive(waitTime=0.1)
            matches = wait_until(
                lambda: [
                    item
                    for item in discover_windows(hwnd=window.hwnd)
                    if item.pid == window.pid
                ],
                timeout=5,
            )
            if len(matches) != 1:
                raise RuntimeError("未能读取所选 QQ 的账号资料，请确认主窗口已登录")
            backend = DesktopBackend(matches[0].nickname, window.hwnd)
            result = DiscoveredWindow(
                hwnd=window.hwnd,
                pid=window.pid,
                nickname=matches[0].nickname,
                account_id=read_self_id(backend),
                avatar=read_self_avatar(backend),
            )
            if window not in scan_windows():
                raise RuntimeError("读取资料期间 QQ 窗口已变化，请刷新探查后重新连接")
            return result
    except Exception as error:
        if is_provider_error(error):
            raise RuntimeError("读取 QQ 账号资料失败，请确认窗口可用后重试") from error
        raise
    finally:
        close_automation(initialized)
