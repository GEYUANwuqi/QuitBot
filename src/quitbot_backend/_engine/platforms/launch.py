"""Resolve the locally configured QQ installation; never accept network commands."""

import os
import shutil
import sys
from pathlib import Path


def qq_command():
    configured = os.environ.get("QUITBOT_QQ_EXECUTABLE")
    if configured:
        candidates = [configured]
    elif sys.platform == "linux":
        candidates = ["/opt/QQ/qq", "qq"]
    elif sys.platform == "win32":
        candidates = []
        import winreg

        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for key in (
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\QQ.exe",
                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\QQ.exe",
            ):
                try:
                    with winreg.OpenKey(hive, key) as handle:
                        candidates.append(winreg.QueryValue(handle, None).strip('"'))
                except OSError:
                    pass
        for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            if os.environ.get(variable):
                candidates.append(
                    str(Path(os.environ[variable]) / "Tencent/QQNT/QQ.exe")
                )
    else:
        raise ValueError("QQ 登录需要 Windows 或 Linux X11 会话")
    for candidate in candidates:
        path = shutil.which(candidate)
        if path and Path(path).is_file():
            return [path, "--force-renderer-accessibility"]
    raise ValueError(
        "未找到官方 QQ，请安装 QQ，或在后端环境中设置 QUITBOT_QQ_EXECUTABLE"
    )
