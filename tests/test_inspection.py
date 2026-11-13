from types import SimpleNamespace

import pytest

from quitbot_backend._engine.errors import ControlUnavailable
from quitbot_backend._engine.platforms.windows.automation import Automation
from quitbot_backend._engine.ui.workflows.inspection import (
    DesktopLocked,
    DesktopUnavailable,
    clipped_reaction_item,
    hover_control,
    require_unlocked,
)


class Control:
    Name = "button"
    ClassName = "button"
    IsOffscreen = False

    def __init__(self, parent=None):
        self.parent = parent

    def GetParentControl(self):
        return self.parent


def windows_provider(**controls):
    provider = Automation.__new__(Automation)
    provider.controls = SimpleNamespace(**controls)
    return provider


def test_disappearing_root_is_refreshed_and_second_observation_still_blocks_lock_screen():
    class Transient(Exception):
        hresult = -2147220991

    reads = [0]
    locked = SimpleNamespace(
        Name="Windows default lock screen", ClassName="Windows.UI.Core.CoreWindow"
    )

    def foreground():
        reads[0] += 1
        if reads[0] == 1:
            raise Transient("window disappeared")
        return locked

    with pytest.raises(DesktopLocked):
        require_unlocked(windows_provider(GetForegroundControl=foreground))
    assert reads[0] == 2


def environment(*, locked=False, occluded=False):
    calls = []
    target = Control()
    target.BoundingRectangle = SimpleNamespace(
        left=10, top=20, width=lambda: 20, height=lambda: 10
    )
    cursor = [(1, 2)]

    def move(**kwargs):
        calls.append("hover")
        cursor[0] = (20, 25)
        return cursor[0]

    target.MoveCursorToMyCenter = move
    front = Control()
    if locked:
        front.Name = "Windows 默认锁屏界面"
        front.ClassName = "Windows.UI.Core.CoreWindow"
    auto = windows_provider(
        GetForegroundControl=lambda: front,
        GetRootControl=lambda: SimpleNamespace(GetChildren=lambda: [front]),
        ControlsAreSame=lambda a, b: a is b,
        ControlFromPoint=lambda x, y: Control() if occluded else target,
        ControlFromCursor=lambda: target,
        GetCursorPos=lambda: cursor[0],
        SetCursorPos=lambda x, y: calls.append(("restore", x, y)),
    )
    backend = SimpleNamespace(
        auto=auto,
        check_thread=lambda: None,
        window=SimpleNamespace(SetActive=lambda **kwargs: calls.append("activate")),
    )
    return backend, target, calls, cursor, front


def test_hover_reacquires_after_activation_without_replaying_pointer_movement():
    backend, target, calls, _, _ = environment()

    class Transient(Exception):
        hresult = -2147220991

    reads = []

    def resolve():
        assert calls == ["activate"]
        reads.append(True)
        if len(reads) == 1:
            raise Transient("old row disappeared")
        return target

    with hover_control(backend, None, resolve=resolve):
        assert len(reads) == 2
    assert calls.count("activate") == calls.count("hover") == 1


def test_locked_desktop_blocks_hover_before_any_input_or_activation():
    backend, target, calls, *_ = environment(locked=True)
    with pytest.raises(DesktopLocked), hover_control(backend, target):
        raise AssertionError("must not enter")
    assert calls == []


def test_unavailable_desktop_blocks_hover_before_activation():
    backend, target, calls, *_ = environment()
    backend.auto.GetForegroundControl = lambda: None
    with pytest.raises(DesktopUnavailable), hover_control(backend, target):
        pass
    assert calls == []


def test_lock_occlusion_is_detected_even_when_foreground_has_no_lock_name():
    backend, *_ = environment()
    occlusion = Control()
    occlusion.ClassName = "LockScreenInputOcclusionFrame"
    backend.auto.GetRootControl = lambda: SimpleNamespace(
        GetChildren=lambda: [occlusion]
    )
    with pytest.raises(DesktopLocked):
        require_unlocked(backend.auto)


def test_hover_uses_observed_control_and_restores_pointer_without_clicking():
    backend, target, calls, *_ = environment()
    with hover_control(backend, target):
        assert calls == ["activate", "hover"]
    assert calls[-1] == ("restore", 1, 2)


def test_occluded_control_never_gets_pointer_movement():
    backend, target, calls, *_ = environment(occluded=True)
    with pytest.raises(ControlUnavailable), hover_control(backend, target):
        pass
    assert calls == ["activate"]


@pytest.mark.parametrize("allow_clipped", [False, True])
def test_offscreen_reaction_requires_verified_clipped_menu_geometry(allow_clipped):
    backend, target, calls, *_ = environment()
    window = backend.window
    window.NativeWindowHandle, window.ProcessId = 10, 20
    window.BoundingRectangle = SimpleNamespace(left=0, top=0, right=600, bottom=900)
    menu = Control(window)
    menu.ControlTypeName = "MenuControl"
    menu.BoundingRectangle = SimpleNamespace(left=0, top=100, right=200, bottom=400)
    target.parent, target.ClassName, target.IsOffscreen = menu, "menu-stickers-item", True
    target.ProcessId, target.GetTopLevelControl = 20, lambda: window
    target.BoundingRectangle.right, target.BoundingRectangle.bottom = 30, 30
    hit = Control()
    hit.ProcessId, hit.GetTopLevelControl = 20, lambda: window
    backend.auto.ControlFromPoint = lambda *_: hit
    backend.auto.ControlFromCursor = lambda: hit
    if allow_clipped:
        with hover_control(backend, target, allow_clipped_reaction=True):
            assert calls.count("hover") == 1
    else:
        with pytest.raises(ControlUnavailable), hover_control(backend, target):
            pass
        assert "hover" not in calls


def test_probe_does_not_override_a_person_or_restore_pointer_after_locking():
    backend, target, calls, cursor, _ = environment()
    with hover_control(backend, target):
        cursor[0] = (100, 100)
    assert not any(isinstance(c, tuple) for c in calls)
    backend, target, calls, _, front = environment()
    with hover_control(backend, target):
        front.Name = "Windows 默认锁屏界面"
        front.ClassName = "Windows.UI.Core.CoreWindow"
    assert not any(isinstance(c, tuple) for c in calls)


def test_clipped_reaction_exception_is_limited_to_same_window_and_menu_ancestry():
    def rect(left, top, right, bottom):
        return SimpleNamespace(left=left, top=top, right=right, bottom=bottom)

    window = SimpleNamespace(
        NativeWindowHandle=10, ProcessId=20, BoundingRectangle=rect(0, 0, 600, 900)
    )
    menu = Control()
    menu.ControlTypeName = "MenuControl"
    menu.BoundingRectangle = rect(10, 100, 200, 400)
    item = Control(menu)
    item.ClassName = "menu-stickers-item"
    item.GetTopLevelControl = lambda: window
    item.ProcessId = 20
    item.BoundingRectangle = rect(20, 60, 50, 90)
    hit = SimpleNamespace(ProcessId=20, GetTopLevelControl=lambda: window)
    assert clipped_reaction_item(item, hit, window)
    item.GetTopLevelControl = lambda: SimpleNamespace(NativeWindowHandle=11)
    assert not clipped_reaction_item(item, hit, window)
    item.GetTopLevelControl = lambda: window
    hit.ProcessId = 21
    assert not clipped_reaction_item(item, hit, window)
    hit.ProcessId = 20
    hit.GetTopLevelControl = lambda: SimpleNamespace(NativeWindowHandle=11)
    assert not clipped_reaction_item(item, hit, window)
    hit.GetTopLevelControl = lambda: window
    item.ClassName = "button"
    assert not clipped_reaction_item(item, hit, window)
    item.ClassName = "menu-stickers-item"
    item.BoundingRectangle = rect(20, 110, 50, 140)
    assert not clipped_reaction_item(item, hit, window)
