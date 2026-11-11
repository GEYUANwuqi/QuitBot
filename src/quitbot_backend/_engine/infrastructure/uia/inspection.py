"""Guarded, control-derived hover for read-only UI investigation."""

import time
from contextlib import contextmanager

from quitbot_backend._engine.errors import ControlUnavailable


class DesktopLocked(ControlUnavailable):
    pass


class DesktopUnavailable(ControlUnavailable):
    pass


def is_lock_surface(control):
    if control is None:
        return False
    name = control.Name.casefold()
    cls = control.ClassName.casefold()
    return "lockscreen" in cls or (
        cls == "windows.ui.core.corewindow"
        and name
        in {"windows 默认锁屏界面", "windows default lock screen", "lock screen"}
    )


def require_unlocked(auto):
    from quitbot_backend._engine.uia import is_transient_uia_error

    for attempt in range(2):
        try:
            return _check_unlocked(auto)
        except Exception as error:
            if attempt or not is_transient_uia_error(error):
                raise
            # A window may disappear between root enumeration and property reads.
            # Refresh the observation before any caller is allowed to send input.
            time.sleep(0.05)


def _check_unlocked(auto):
    foreground = auto.GetForegroundControl()
    if foreground is None:
        raise DesktopUnavailable(
            "No foreground window is available; restore the interactive Windows desktop before UI input"
        )
    if is_lock_surface(foreground):
        raise DesktopLocked(
            "Windows lock screen is foreground; unlock the desktop before UI investigation"
        )
    for window in auto.GetRootControl().GetChildren():
        if is_lock_surface(window) and not window.IsOffscreen:
            raise DesktopLocked(
                "Windows lock-screen occlusion is present; no UI input performed"
            )


def is_descendant(auto, control, ancestor):
    for _ in range(64):
        if control is None:
            return False
        if auto.ControlsAreSame(control, ancestor):
            return True
        control = control.GetParentControl()
    return False


def clipped_reaction_item(control, hit, window):
    """QQ reports sticker items above their menu's AX hit-test rectangle."""
    if "menu-stickers-item" not in control.ClassName.split() or hit is None:
        return False
    menu = control.GetParentControl()
    for _ in range(16):
        if menu is None:
            return False
        if getattr(menu, "ControlTypeName", "") == "MenuControl":
            break
        menu = menu.GetParentControl()
    else:
        return False
    rect, bounds, owner = (
        control.BoundingRectangle,
        menu.BoundingRectangle,
        window.BoundingRectangle,
    )
    clipped = (
        rect.top < bounds.top
        or rect.left < bounds.left
        or rect.bottom > bounds.bottom
        or rect.right > bounds.right
    )
    inside_window = (
        owner.left <= rect.left < rect.right <= owner.right
        and owner.top <= rect.top < rect.bottom <= owner.bottom
    )
    top = hit.GetTopLevelControl()
    target_top = control.GetTopLevelControl()
    return (
        clipped
        and inside_window
        and not menu.IsOffscreen
        and target_top.NativeWindowHandle == window.NativeWindowHandle
        and control.ProcessId == window.ProcessId
        and top.NativeWindowHandle == window.NativeWindowHandle
        and hit.ProcessId == window.ProcessId
    )


@contextmanager
def hover_control(backend, control, *, allow_clipped_reaction=False, resolve=None):
    """Hover the current UIA rectangle, with hit testing and no mouse click.

    UIA has no HoverPattern. The control supplies its current rectangle; pointer
    movement is only used to reveal hover-only controls during investigation.
    """
    auto = backend.auto
    backend.check_thread()
    require_unlocked(auto)
    backend.window.SetActive(waitTime=0.05)
    require_unlocked(auto)
    if resolve is not None:
        from quitbot_backend._engine.uia import wait_until

        def fresh():
            candidate = resolve()
            return candidate, candidate.BoundingRectangle, candidate.IsOffscreen

        # Activating or returning to a folder can rebuild QQ's rows. Retry only
        # native reads; pointer movement and business actions are never replayed.
        control, rect, offscreen = wait_until(fresh)
    else:
        rect, offscreen = control.BoundingRectangle, control.IsOffscreen
    if offscreen or rect.width() <= 0 or rect.height() <= 0:
        raise ControlUnavailable("Hover target has no visible UIA rectangle")
    point = (rect.left + rect.width() // 2, rect.top + rect.height() // 2)
    hit = auto.ControlFromPoint(*point)
    if is_lock_surface(hit):
        raise DesktopLocked("Hover point is covered by the Windows lock screen")
    exact = is_descendant(auto, hit, control)
    clipped = allow_clipped_reaction and clipped_reaction_item(
        control, hit, backend.window
    )
    if not exact and not clipped:
        raise ControlUnavailable(
            "Hover target is occluded or hit testing does not identify its UIA subtree"
        )
    previous = auto.GetCursorPos()
    moved = None
    try:
        moved = control.MoveCursorToMyCenter(simulateMove=False)
        require_unlocked(auto)
        current = auto.ControlFromCursor()
        if moved is None or not (
            is_descendant(auto, current, control)
            or allow_clipped_reaction
            and clipped_reaction_item(control, current, backend.window)
        ):
            raise ControlUnavailable("Pointer did not land on the observed control")
        yield {
            "hit_test": "exact_subtree" if exact else "clipped_reaction_same_window",
            "mouse_click": False,
        }
    finally:
        # Do not fight a person who has moved the pointer during inspection.
        unlocked = True
        try:
            require_unlocked(auto)
        except DesktopLocked:
            unlocked = False
        if unlocked and moved is not None and auto.GetCursorPos() == moved:
            auto.SetCursorPos(*previous)
