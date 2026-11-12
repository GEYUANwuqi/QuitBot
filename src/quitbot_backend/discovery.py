"""Process discovery and explicit profile reads in the current graphical session."""

import ctypes
import ntpath
import os
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
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.ProcessIdToSessionId.argtypes = [
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.ProcessIdToSessionId.restype = wintypes.BOOL
    session = wintypes.DWORD()
    if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise ctypes.WinError(ctypes.get_last_error())
    return session.value


def scan_windows() -> list[DiscoveredProcess]:
    """Read Win32 window/process metadata without UIA, focus or profile access."""
    if sys.platform == "linux":
        return _scan_linux()
    if sys.platform != "win32":
        raise RuntimeError("QQ 窗口探查需要 Windows 或 Linux X11 会话")
    from ctypes import wintypes

    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows.restype = wintypes.BOOL
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsWindowVisible.restype = wintypes.BOOL
    for name in ("GetWindowTextW", "GetClassNameW"):
        function = getattr(user, name)
        function.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        function.restype = ctypes.c_int
    user.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.ProcessIdToSessionId.argtypes = [
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.ProcessIdToSessionId.restype = wintypes.BOOL
    current_session = windows_session_id()
    result = []

    @callback_type
    def visit(hwnd, _):
        if not user.IsWindowVisible(hwnd):
            return True
        title, class_name = (
            ctypes.create_unicode_buffer(256),
            ctypes.create_unicode_buffer(256),
        )
        user.GetWindowTextW(hwnd, title, len(title))
        user.GetClassNameW(hwnd, class_name, len(class_name))
        if title.value != "QQ" or class_name.value != "Chrome_WidgetWin_1":
            return True
        pid, session = wintypes.DWORD(), wintypes.DWORD()
        if not user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            return True
        if not kernel.ProcessIdToSessionId(pid.value, ctypes.byref(session)):
            return True
        if session.value != current_session:
            return True
        process = kernel.OpenProcess(0x1000, False, pid.value)
        if not process:
            return True
        try:
            path = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(path))
            if kernel.QueryFullProcessImageNameW(
                process, 0, path, ctypes.byref(length)
            ):
                name = ntpath.basename(path.value)
                if name.lower() == "qq.exe":
                    result.append(
                        DiscoveredProcess(hwnd=hwnd, pid=pid.value, process_name=name)
                    )
        finally:
            kernel.CloseHandle(process)
        return True

    if not user.EnumWindows(visit, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return sorted(result, key=lambda window: (window.pid, window.hwnd))


def read_window_profile(window: DiscoveredProcess) -> DiscoveredWindow:
    """Resolve only the selected window, keeping COM and UI cleanup on one thread."""
    if window not in scan_windows():
        raise RuntimeError("所选 QQ 进程或窗口已变化，请刷新探查后重新连接")
    from ._engine.native import automation

    auto = automation()
    if sys.platform == "win32":
        from comtypes import COMError
    else:
        COMError = ()

    from ._engine.discovery import discover_windows, read_self_avatar, read_self_id
    from ._engine.infrastructure.uia.inspection import require_unlocked
    from ._engine.locking import ui_transaction
    from ._engine.uia import UIABackend, wait_until

    if sys.platform == "win32":
        auto.InitializeUIAutomationInCurrentThread()
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
            backend = UIABackend(matches[0].nickname, window.hwnd)
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
    except COMError as error:
        raise RuntimeError("读取 QQ 账号资料失败，请确认窗口可用后重试") from error
    finally:
        if sys.platform == "win32":
            auto.UninitializeUIAutomationInCurrentThread()


def _scan_linux():
    from pathlib import Path

    from ._engine.infrastructure.atspi.x11 import Desktop

    desktop = Desktop()
    try:
        result = []
        for window in desktop.windows():
            if not window.mapped or window.title != "QQ":
                continue
            try:
                process = Path(f"/proc/{window.pid}")
                if process.stat().st_uid != os.getuid():
                    continue
                name = (process / "exe").resolve(strict=True).name
                if name.lower() == "qq":
                    result.append(DiscoveredProcess(hwnd=window.id, pid=window.pid, process_name=name))
            except (OSError, RuntimeError):
                continue
        return sorted(result, key=lambda w: (w.pid, w.hwnd))
    finally:
        desktop.close_connection()
