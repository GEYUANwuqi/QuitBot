"""Discovery caching and native cleanup; all windows are synthetic."""

import asyncio
import sys
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from quitbot_backend.auth import AccessKeys
from quitbot_backend.config import ConfigStore
from quitbot_backend.discovery import (
    DiscoveredProcess,
    read_window_profile,
    scan_windows,
)
from quitbot_backend.service import Service


def test_discovery_coalesces_refresh_and_finishes_after_caller_cancellation(tmp_path):
    async def run():
        entered, release = threading.Event(), threading.Event()
        calls = []

        def discover():
            calls.append(True)
            if len(calls) == 1:
                entered.set()
                assert release.wait(3)
            if len(calls) == 2:
                raise OSError("fixture desktop unavailable")
            return [DiscoveredProcess(hwnd=101, pid=201)]

        service = Service(
            ConfigStore(tmp_path / "accounts.json"), AccessKeys(), discover=discover
        )
        try:
            startup = service.start_discovery()
            assert await asyncio.to_thread(entered.wait, 2)
            assert service.start_discovery() is startup
            follower = asyncio.create_task(service.refresh_discovery())
            await asyncio.sleep(0)
            follower.cancel()
            with pytest.raises(asyncio.CancelledError):
                await follower
            release.set()
            first = await startup
            assert first.state == "ready" and len(calls) == 1
            assert service.discovery is first
            failed = await service.refresh_discovery()
            assert failed.state == "failed" and failed.windows == []
            assert "fixture desktop" in failed.error
            retried = await service.refresh_discovery()
            assert retried.windows == first.windows and len(calls) == 3
        finally:
            release.set()
            await service.close()

    asyncio.run(run())


def test_profile_read_targets_one_process_and_releases_com_on_failure(monkeypatch):
    from quitbot_backend import discovery
    from quitbot_backend._engine import discovery as native
    from quitbot_backend._engine import locking, uia
    from quitbot_backend._engine.infrastructure.uia import inspection

    calls = []
    windows = [
        DiscoveredProcess(hwnd=101, pid=201),
        DiscoveredProcess(hwnd=102, pid=202),
    ]
    monkeypatch.setattr(discovery, "scan_windows", lambda: windows)
    monkeypatch.setattr(discovery.sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules,
        "uiautomation",
        SimpleNamespace(
            InitializeUIAutomationInCurrentThread=lambda: calls.append("init"),
            UninitializeUIAutomationInCurrentThread=lambda: calls.append("close"),
            ControlFromHandle=lambda hwnd: SimpleNamespace(
                SetActive=lambda **_: calls.append(("activate", hwnd))
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "comtypes",
        SimpleNamespace(COMError=type("COMError", (Exception,), {})),
    )
    monkeypatch.setattr(locking, "ui_transaction", nullcontext)
    monkeypatch.setattr(inspection, "require_unlocked", lambda _: None)
    attempts = []

    def discover(*, hwnd):
        attempts.append(hwnd)
        if attempts.count(hwnd) == 1:
            return []  # The selected window's controls are still loading.
        return [native.QQWindow(hwnd, hwnd + 100, "same-name")]

    monkeypatch.setattr(native, "discover_windows", discover)
    monkeypatch.setattr(uia, "UIABackend", lambda nickname, hwnd: hwnd)

    def identify(hwnd):
        calls.append(hwnd)
        if hwnd == 101:
            raise RuntimeError("fixture window closed")
        return "10002"

    monkeypatch.setattr(native, "read_self_id", identify)
    monkeypatch.setattr(
        native, "read_self_avatar", lambda _: "data:image/png;base64,fixture"
    )
    with pytest.raises(RuntimeError, match="fixture window closed"):
        read_window_profile(windows[0])
    result = read_window_profile(windows[1])
    assert result.account_id == "10002" and result.hwnd == 102 and result.available
    assert calls == [
        "init",
        ("activate", 101),
        101,
        "close",
        "init",
        ("activate", 102),
        102,
        "close",
    ]
    assert attempts == [101, 101, 102, 102]

    before = list(calls)
    with pytest.raises(RuntimeError, match="进程或窗口已变化"):
        read_window_profile(DiscoveredProcess(hwnd=102, pid=203))
    assert calls == before

    def unavailable(_):
        raise RuntimeError("fixture desktop locked")

    monkeypatch.setattr(inspection, "require_unlocked", unavailable)
    with pytest.raises(RuntimeError, match="fixture desktop locked"):
        read_window_profile(windows[1])
    assert calls[-2:] == ["init", "close"]


def test_process_scan_filters_session_and_executable_without_uia(monkeypatch):
    from quitbot_backend import discovery

    # Win32 functions only: loading UIA during enumeration would fail this scan.
    monkeypatch.setattr(discovery.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "uiautomation", None)
    monkeypatch.setattr(discovery, "windows_session_id", lambda: 7)
    monkeypatch.setattr(
        discovery.ctypes, "WINFUNCTYPE", discovery.ctypes.CFUNCTYPE, raising=False
    )
    closed = []

    def enumerate_windows(visit, data):
        for hwnd in range(101, 108):
            assert visit(hwnd, data)
        return True

    def title(hwnd, buffer, _):
        buffer.value = "Settings" if hwnd == 105 else "QQ"
        return len(buffer.value)

    def class_name(hwnd, buffer, _):
        buffer.value = "OtherClass" if hwnd == 106 else "Chrome_WidgetWin_1"
        return len(buffer.value)

    def process_id(hwnd, output):
        output._obj.value = hwnd + 100
        return 1

    def session_id(pid, output):
        output._obj.value = 8 if pid == 203 else 7
        return True

    def image_name(process, _, output, length):
        output.value = (
            r"C:\fixture\Other.exe" if process == 202 else r"C:\fixture\QQ.exe"
        )
        length._obj.value = len(output.value)
        return True

    user = SimpleNamespace(
        EnumWindows=enumerate_windows,
        IsWindowVisible=lambda hwnd: hwnd != 107,
        GetWindowTextW=title,
        GetClassNameW=class_name,
        GetWindowThreadProcessId=process_id,
    )
    kernel = SimpleNamespace(
        OpenProcess=lambda access, inherit, pid: 0 if pid == 204 else pid,
        CloseHandle=lambda handle: closed.append(handle) or True,
        QueryFullProcessImageNameW=image_name,
        ProcessIdToSessionId=session_id,
    )
    monkeypatch.setattr(
        discovery.ctypes,
        "WinDLL",
        lambda name, **_: user if name == "user32" else kernel,
        raising=False,
    )
    assert scan_windows() == [DiscoveredProcess(hwnd=101, pid=201)]
    assert closed == [201, 202]
