from types import SimpleNamespace

import pytest

from quitbot_backend._engine.discovery import QQWindow, profile_qq_id, select_window
from quitbot_backend._engine.errors import ControlUnavailable, TargetMismatch
from quitbot_backend._engine.models import Target
from quitbot_backend._engine.ui.backend import DesktopBackend, DetachedWindow


def test_profile_identity_requires_qq_label_not_arbitrary_digits():
    assert profile_qq_id(["9999+", "QQ ", "30002", "19岁"]) == "30002"
    assert profile_qq_id(["QQ 10001"]) == "10001"
    with pytest.raises(ControlUnavailable):
        profile_qq_id(["9999+", "30002"])


def test_own_profile_waits_for_labelled_identity_without_replaying_activation(
    monkeypatch,
):
    import quitbot_backend._engine.discovery as module

    opened = [False]
    reads = [0]
    calls = []
    card = SimpleNamespace(
        Name="资料卡",
        ProcessId=10,
        GetWindowPattern=lambda: SimpleNamespace(
            Close=lambda **_: calls.append("close")
        ),
    )

    def send(*args, **kwargs):
        opened[0] = True
        calls.append("activate")

    def walk(_):
        reads[0] += 1
        names = ["9999+"] if reads[0] == 1 else ["QQ ", "10001"]
        return [SimpleNamespace(ControlTypeName="TextControl", Name=n) for n in names]

    original = object()
    main = SimpleNamespace(ProcessId=10)
    backend = SimpleNamespace(
        window=original,
        main_window=main,
        check_thread=lambda: None,
        auto=SimpleNamespace(
            GetRootControl=lambda: SimpleNamespace(
                GetChildren=lambda: [card] if opened[0] else []
            ),
            SendKeys=send,
        ),
        find=lambda *_: object(),
        focus_control=lambda _: None,
        walk=walk,
    )
    monkeypatch.setattr(module, "require_unlocked", lambda _: None)
    assert module.read_self_id(backend) == "10001"
    assert (
        reads[0] == 2 and calls == ["activate", "close"] and backend.window is original
    )


def test_discovery_selection_rejects_duplicate_nicknames_and_stale_handles():
    windows = [QQWindow(1, 10, "same", "12345"), QQWindow(2, 20, "same", "23456")]
    assert select_window(windows, "23456").hwnd == 2
    assert select_window(windows, "same", hwnd=1).qq_id == "12345"
    with pytest.raises(ControlUnavailable):
        select_window(windows, "same")
    with pytest.raises(ControlUnavailable):
        select_window(windows, "23456", hwnd=1)


def test_avatar_waits_for_rendering_after_one_focus(monkeypatch):
    import quitbot_backend._engine.discovery as module

    monkeypatch.setattr(module, "require_unlocked", lambda _: None)
    focused, captures = [], []
    image = b"\x89PNG\r\n\x1a\nfixture"

    def bitmap():
        captures.append(True)
        if len(captures) == 1:
            return None
        return SimpleNamespace(
            ToBytes=lambda format: (
                image
                if format == "png"
                else b"\0\0\0\xff" * 4
                if len(captures) == 2
                else b"\0\0\0\xff\xff\xff\xff\xff"
            )
        )

    avatar = SimpleNamespace(
        IsOffscreen=False,
        BoundingRectangle=SimpleNamespace(width=lambda: 40, height=lambda: 40),
        ToBitmap=bitmap,
    )
    backend = SimpleNamespace(
        check_thread=lambda: None,
        auto=None,
        window=object(),
        account="fixture",
        find=lambda *_, **__: avatar,
        focus_control=lambda control, **_: focused.append(control),
    )
    assert module.read_self_avatar(backend).startswith("data:image/png;base64,")
    assert focused == [avatar] and len(captures) == 3


def test_closed_detached_window_is_not_replaced_by_same_name():
    backend = DesktopBackend.__new__(DesktopBackend)
    backend._detached = {"1": DetachedWindow("1", "测试群", 100, 10)}
    backend.chat_windows = lambda target: [SimpleNamespace(NativeWindowHandle=200)]
    with pytest.raises(ControlUnavailable):
        backend.activate_detached(Target("1", "测试群"), required=True)


def test_ambiguous_detached_window_is_not_selected():
    backend = DesktopBackend.__new__(DesktopBackend)
    backend.chat_windows = lambda target: [object(), object()]
    with pytest.raises(TargetMismatch):
        backend.activate_detached(Target("1", "测试群"))
