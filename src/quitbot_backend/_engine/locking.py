"""Serialize desktop transactions across SDK clients and Python processes."""

import ctypes
import hashlib
import os
import re
import sys
import tempfile
import threading
import time
from contextlib import contextmanager

_THREAD_LOCK = threading.RLock()
_LOCAL = threading.local()


@contextmanager
def ui_transaction():
    with _THREAD_LOCK:
        if sys.platform != "win32":
            with _posix_transaction():
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


@contextmanager
def _posix_transaction():
    import fcntl

    if getattr(_LOCAL, "depth", 0):
        _LOCAL.depth += 1
        try:
            yield
        finally:
            _LOCAL.depth -= 1
        return
    # Every process using this display shares input focus and the clipboard.
    display = re.sub(r"(:\d+)\.\d+$", r"\1", os.environ.get("DISPLAY", "default"))
    key = hashlib.sha256(display.encode()).hexdigest()[:16]
    directory = os.path.join(tempfile.gettempdir(), f"quitbot-desktop-{os.getuid()}")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    info = os.stat(directory, follow_symlinks=False)
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError("Unsafe desktop lock directory")
    fd = os.open(os.path.join(directory, key + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Another QuitBot process is using the desktop")
                time.sleep(0.05)
        _LOCAL.depth = 1
        try:
            yield
        finally:
            _LOCAL.depth = 0
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
