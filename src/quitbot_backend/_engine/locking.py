"""Serialize complete desktop operations across threads and processes."""

import sys
import threading
from contextlib import contextmanager

_THREAD_LOCK = threading.RLock()


@contextmanager
def ui_transaction():
    if sys.platform == "win32":
        from .platforms.windows.locking import transaction
    elif sys.platform == "linux":
        from .platforms.linux.locking import transaction
    else:
        raise OSError("Desktop transactions require Windows or Linux")
    with _THREAD_LOCK, transaction():
        yield
