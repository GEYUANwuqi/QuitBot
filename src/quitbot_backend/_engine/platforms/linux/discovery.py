"""Discover QQ processes owned by the current X11 session user."""

import os
from pathlib import Path


def scan_windows():
    from .x11 import Desktop

    desktop = Desktop()
    try:
        result = []
        managed = desktop.property(desktop.root, "_NET_CLIENT_LIST")
        managed = set(managed) if managed is not None else set()
        for window in desktop.windows():
            if window.title != "QQ" or not (window.mapped or window.id in managed):
                continue
            try:
                process = Path(f"/proc/{window.pid}")
                if process.stat().st_uid != os.getuid():
                    continue
                name = (process / "exe").resolve(strict=True).name
                if name.lower() == "qq":
                    result.append({"hwnd": window.id, "pid": window.pid, "process_name": name})
            except (OSError, RuntimeError):
                continue
        return sorted(result, key=lambda w: (w["pid"], w["hwnd"]))
    finally:
        desktop.close_connection()
