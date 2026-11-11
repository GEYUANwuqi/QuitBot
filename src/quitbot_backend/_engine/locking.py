"""Serialize desktop transactions across SDK clients and Python processes."""

import ctypes
import sys
import threading
from contextlib import contextmanager

_THREAD_LOCK = threading.RLock()


@contextmanager
def ui_transaction():
    with _THREAD_LOCK:
        if sys.platform != "win32":
            yield
            return
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateMutexW(None, False, "Local\\QuitBot.UIAutomation.v1")
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = False
        try:
            status = kernel.WaitForSingleObject(handle, 30000)
            if status not in (0, 0x80):
                raise TimeoutError("Another QuitBot process is using the desktop")
            acquired = True
            yield
        finally:
            if acquired:
                kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
