"""Discover account windows; numeric identity is read from QQ's own profile."""

import base64
import re
from dataclasses import dataclass

from .errors import ControlUnavailable
from .locking import ui_transaction
from .ui.backend import DesktopBackend, navigation_label_matches, wait_until
from .ui.workflows.inspection import (
    DesktopLocked,
    DesktopUnavailable,
    require_unlocked,
)
from .ui.workflows.profile_cards import ProfileCards


@dataclass(frozen=True)
class QQWindow:
    hwnd: int
    pid: int
    nickname: str
    qq_id: str | None = None


def profile_qq_id(texts):
    for index, text in enumerate(texts):
        match = re.fullmatch(r"QQ\s*(\d{5,12})", text.strip())
        if match:
            return match[1]
        if text.strip() == "QQ" and index + 1 < len(texts):
            number = texts[index + 1].strip()
            if re.fullmatch(r"\d{5,12}", number):
                return number
    raise ControlUnavailable("Own profile did not expose a labelled QQ number")


def read_self_id(backend):
    backend.check_thread()
    require_unlocked(backend.auto)
    main = getattr(backend, "main_window", backend.window)
    avatar = backend.find(
        main,
        lambda x: (
            x.ControlTypeName == "ButtonControl"
            and "user-avatar" in x.ClassName.split()
        ),
    )
    # SetFocus uses the main window, even when a chat is currently detached.
    previous = backend.window
    backend.window = main
    try:
        probe = ProfileCards(backend)

        def own_card():
            def labelled(fields):
                try:
                    return bool(profile_qq_id(fields))
                except ControlUnavailable:
                    return False

            return probe.refreshed(labelled)

        neutral = backend.find(
            main,
            lambda x: (
                x.ControlTypeName == "ButtonControl"
                and "nav-item" in x.ClassName.split()
                and navigation_label_matches(x.Name, "消息")
            ),
        )
        backend.focus_control(neutral)
        backend.focus_control(avatar)
        backend.auto.SendKeys("{Enter}", waitTime=0.1)
        try:
            result = wait_until(own_card, timeout=2)
        except ControlUnavailable:
            backend.invoke(avatar)
            result = wait_until(own_card, timeout=3)
        card = probe.reveal(result)
        try:
            return profile_qq_id(result[2])
        finally:
            try:
                require_unlocked(backend.auto)
            except (DesktopLocked, DesktopUnavailable):
                pass
            else:
                card.GetWindowPattern().Close(waitTime=0.05)
    finally:
        backend.window = previous


def read_self_avatar(backend):
    """Capture only the identified UIA avatar control; keep image data in memory."""
    backend.check_thread()
    require_unlocked(backend.auto)
    main = getattr(backend, "main_window", backend.window)

    def find_avatar():
        return backend.find(
            main,
            lambda x: (
                x.ControlTypeName == "ButtonControl"
                and "user-avatar" in x.ClassName.split()
                and x.Name == f"{backend.account}的头像"
            ),
            required=False,
        )

    avatar = wait_until(find_avatar, timeout=5)
    backend.focus_control(avatar, resolve=find_avatar)

    def capture():
        avatar = find_avatar()
        if avatar is None:
            return None
        rect = avatar.BoundingRectangle
        if avatar.IsOffscreen or not (
            16 <= rect.width() <= 512 and 16 <= rect.height() <= 512
        ):
            return None
        bitmap = avatar.ToBitmap()
        if bitmap is None:
            return None
        pixels = bitmap.ToBytes("bgra")
        if len({bytes(pixels[i : i + 3]) for i in range(0, len(pixels), 4)}) < 2:
            return None
        image = bytes(bitmap.ToBytes("png"))
        return (
            image
            if image.startswith(b"\x89PNG\r\n\x1a\n") and len(image) <= 512_000
            else None
        )

    try:
        image = wait_until(capture, timeout=5)
    except ControlUnavailable as error:
        raise ControlUnavailable(
            "QQ 头像尚未完整显示，请展开主窗口后重新连接"
        ) from error
    return "data:image/png;base64," + base64.b64encode(image).decode("ascii")


def discover_windows(*, resolve_ids=False, hwnd=None):
    """ID resolution opens each account's own profile and changes focus."""
    from .platforms import automation

    auto = automation()

    result = []
    with ui_transaction():
        for window in auto.GetRootControl().GetChildren():
            if hwnd is not None and window.NativeWindowHandle != hwnd:
                continue
            if window.Name != "QQ" or window.ClassName != "Chrome_WidgetWin_1":
                continue
            b = DesktopBackend.__new__(DesktopBackend)
            b.auto = auto
            avatar = b.find(
                window,
                lambda c: (
                    c.ControlTypeName == "ButtonControl"
                    and "user-avatar" in c.ClassName.split()
                ),
                required=False,
            )
            if avatar is None or not avatar.Name.endswith("的头像"):
                continue
            nickname = avatar.Name.removesuffix("的头像")
            number = None
            if resolve_ids:
                bound = DesktopBackend(nickname, window.NativeWindowHandle)
                number = read_self_id(bound)
            result.append(
                QQWindow(window.NativeWindowHandle, window.ProcessId, nickname, number)
            )
    return result


def select_window(windows, account=None, *, hwnd=None):
    matches = [
        w
        for w in windows
        if (hwnd is None or w.hwnd == hwnd)
        and (account is None or str(account) in (w.nickname, w.qq_id))
    ]
    if len(matches) != 1:
        raise ControlUnavailable(
            f"Expected one discovered account window, found {len(matches)}"
        )
    return matches[0]
