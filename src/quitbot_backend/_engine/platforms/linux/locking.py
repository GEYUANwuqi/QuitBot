"""Process-shared X11 display lock."""

import hashlib
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager

_LOCAL = threading.local()


@contextmanager
def transaction():
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
