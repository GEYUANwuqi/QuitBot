"""Discover QQ processes in the current Windows session without UIA."""

import ctypes
import ntpath
import os


def windows_session_id() -> int | None:
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


def scan_windows():
    """Read Win32 window/process metadata without UIA, focus or profile access."""
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
                        {"hwnd": hwnd, "pid": pid.value, "process_name": name}
                    )
        finally:
            kernel.CloseHandle(process)
        return True

    if not user.EnumWindows(visit, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return sorted(result, key=lambda window: (window["pid"], window["hwnd"]))
