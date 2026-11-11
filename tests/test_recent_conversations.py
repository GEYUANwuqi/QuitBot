"""Loaded conversation discovery, identity reads and menu recovery, without QQ."""

from types import SimpleNamespace

import pytest

from quitbot_backend._engine.errors import ControlUnavailable, TargetMismatch
from quitbot_backend._engine.infrastructure.uia.recent import RecentConversations
from quitbot_backend._engine.models import Target
from quitbot_backend._engine.uia import UIABackend


def control(kind="GroupControl", name="", cls="", children=(), focus=False):
    return SimpleNamespace(
        ControlTypeName=kind,
        Name=name,
        ClassName=cls,
        IsOffscreen=False,
        IsKeyboardFocusable=focus,
        children=list(children),
        ProcessId=10,
        NativeWindowHandle=0,
    )


@pytest.fixture
def recent_ui(monkeypatch):
    from quitbot_backend._engine.infrastructure.uia import recent as module

    state = {
        "keyboard_menu": False,
        "sequence": 10,
        "copy_changes": True,
        "label": "复制群号",
        "number": "20001",
    }
    calls = []
    content = control(cls="item__content", focus=True)
    row = control(
        cls="recent-contact-item",
        children=(
            control("TextControl", "fixture group"),
            content,
        ),
    )
    pane = control("PaneControl", "会话列表", children=(row,))
    menu = control(
        "MenuControl", children=(control("MenuItemControl", state["label"]),)
    )
    main = control(children=(pane,))
    main.NativeWindowHandle = 100
    main.SetActive = lambda **_: calls.append("activate")
    previous = object()
    b = UIABackend.__new__(UIABackend)
    b.window, b.main_window = previous, main
    b.check_thread = lambda: None
    b._require_no_modal = lambda: None
    b.navigate = lambda _: b.use_main_window()

    def walk(c):
        yield c
        for child in c.children:
            yield from walk(child)

    b.walk = walk

    def focus(c, *, resolve):
        assert c is content
        # Simulate Chromium replacing the row's tab stop when focused.
        replacement = control(cls="item__content", focus=True)
        row.children[-1] = replacement
        assert resolve() is replacement
        calls.append("focus-new-element")

    b.focus_control = focus

    def show_native(c):
        assert c is row.children[-1]
        calls.append("native-menu")
        main.children.append(menu)

    def send(key, **_):
        calls.append(key)
        if key == "{Shift}{F10}" and state["keyboard_menu"]:
            main.children.append(menu)
        elif key == "{Esc}":
            main.children.remove(menu)

    def invoke(item):
        calls.append(item.Name)
        if state["copy_changes"]:
            state["sequence"] += 1
        main.children.remove(menu)

    b.invoke = invoke
    b.auto = SimpleNamespace(
        SendKeys=send,
        GetRootControl=lambda: SimpleNamespace(GetChildren=lambda: [main]),
        GetClipboardText=lambda: state["number"],
    )

    def immediate(read, predicate=bool, **_):
        value = read()
        if predicate(value):
            return value
        raise ControlUnavailable("not ready")

    monkeypatch.setattr(module, "wait_until", immediate)
    monkeypatch.setattr(module, "require_unlocked", lambda _: None)
    monkeypatch.setattr(
        module.ctypes,
        "windll",
        SimpleNamespace(
            user32=SimpleNamespace(
                GetClipboardSequenceNumber=lambda: state["sequence"],
            )
        ),
        raising=False,
    )
    monkeypatch.setattr(RecentConversations, "show_native", staticmethod(show_native))
    return RecentConversations(b), state, calls, row, pane, menu, previous


def test_discovery_reads_names_only_and_blocks_ambiguous_rows_before_input(recent_ui):
    recent, _, calls, row, pane, _, previous = recent_ui
    pane.children.append(row)
    found = recent.discover()
    assert (
        len(found) == 1 and found[0].name == "fixture group" and not found[0].selectable
    )
    assert recent.b.window is previous and calls == []
    with pytest.raises(TargetMismatch, match="同名"):
        recent.resolve("fixture group")
    assert recent.b.window is previous and calls == []


@pytest.mark.parametrize("present", [True, False])
def test_recent_locator_scrolls_virtual_list_and_restores_position_on_failure(
    recent_ui, present
):
    recent, _, calls, row, pane, _, _ = recent_ui
    recent.b.window = recent.b.main_window
    pane.children.clear()
    recent.b.auto.PatternId = SimpleNamespace(ScrollPattern=1)
    scroll = SimpleNamespace(
        VerticallyScrollable=True, VerticalScrollPercent=12.5, VerticalViewSize=10
    )

    def move(_, position, **__):
        calls.append(position)
        scroll.VerticalScrollPercent = position
        pane.children = [row] if present and position >= 30 else []

    scroll.SetScrollPercent = move
    pane.GetPattern = lambda _: scroll
    if present:
        assert recent.locate("fixture group") is row
        assert calls[0] == 0 and scroll.VerticalScrollPercent >= 30
    else:
        with pytest.raises(ControlUnavailable, match="未在 QQ 最近会话列表中找到"):
            recent.locate("fixture group")
        assert 100 in calls and calls[-1] == 12.5
    assert not any(isinstance(call, str) for call in calls)


@pytest.mark.parametrize(
    "kind,label,keyboard",
    [
        ("group", "复制群号", False),
        ("private", "复制QQ号", True),
    ],
)
def test_resolve_uses_fresh_focus_and_only_falls_back_when_menu_is_absent(
    recent_ui, kind, label, keyboard
):
    recent, state, calls, _, _, menu, previous = recent_ui
    menu.children[0].Name = label
    state["keyboard_menu"] = keyboard
    result = recent.resolve("fixture group")
    assert result == Target("20001", "fixture group", kind)
    assert calls.count(label) == 1 and calls.count("{Shift}{F10}") == 1
    assert calls.count("native-menu") == (0 if keyboard else 1)
    assert recent.b.window is previous and menu not in recent.b.main_window.children


@pytest.mark.parametrize("problem", ["stale_clipboard", "invalid_number", "non_chat"])
def test_identity_read_never_accepts_stale_clipboard_or_service_conversations(
    recent_ui, problem
):
    recent, state, calls, _, _, menu, previous = recent_ui
    if problem == "stale_clipboard":
        state["copy_changes"] = False
    elif problem == "invalid_number":
        state["number"] = "last copied text 20001"
    else:
        menu.children[0].Name = "服务设置"
    with pytest.raises(ControlUnavailable):
        recent.resolve("fixture group")
    assert recent.b.window is previous and menu not in recent.b.main_window.children
    if problem == "non_chat":
        assert "服务设置" not in calls and calls.count("{Esc}") == 1


def test_detaching_reuses_window_opened_by_qq_and_reports_failed_stage(monkeypatch):
    from quitbot_backend._engine.infrastructure.uia.recent import RecentConversations

    target = Target("20001", "fixture group")
    calls = []
    main = SimpleNamespace(SetActive=lambda **_: None)
    detached = SimpleNamespace(NativeWindowHandle=200, ProcessId=10)
    b = UIABackend.__new__(UIABackend)
    b.window = b.main_window = main
    b._detached = {}
    b.check_thread = lambda: None
    opened = []
    b.open_target = lambda _: opened.append(True)

    def activate(*_, **__):
        if opened:
            b.window = detached
        return bool(opened)

    def profile(*_):
        calls.append("verify")

    b.activate_detached, b.profile = activate, profile
    b.chat_windows = lambda _: [detached]
    monkeypatch.setattr(
        RecentConversations, "menu", lambda *_: pytest.fail("Already detached")
    )
    assert b.open_detached(target).hwnd == 200
    assert b.open_detached(target).hwnd == 200
    assert opened == [True] and calls == ["verify", "verify"]
    b._detached.clear()
    b.profile = lambda *_: (_ for _ in ()).throw(TargetMismatch("wrong code"))
    with pytest.raises(TargetMismatch, match="fixture group.*核验独立窗口.*wrong code"):
        b.open_detached(target)
    assert not b._detached


def test_detaching_opens_menu_action_once_and_only_caches_verified_window(recent_ui):
    recent, _, calls, _, _, menu, _ = recent_ui
    b = recent.b
    b.window = b.main_window
    b._detached = {}
    windows = []
    detached = SimpleNamespace(NativeWindowHandle=200, ProcessId=10)
    menu.children[0].Name = "打开独立聊天窗口"
    b.open_target = lambda _: calls.append("open-target")
    b.profile = lambda *args: calls.append(("profile", b.window))
    b.chat_windows = lambda _: windows
    invoke = b.invoke

    def open_window(item):
        invoke(item)
        windows.append(detached)

    b.invoke = open_window
    info = b.open_detached(Target("20001", "fixture group"))
    assert info.hwnd == 200 and b._detached["20001"] == info
    assert calls.count("打开独立聊天窗口") == 1
    assert ("profile", b.main_window) in calls and ("profile", detached) in calls
    assert b.window is detached and menu not in b.main_window.children


def test_open_session_waits_for_remounted_header_without_replaying_selection(recent_ui):
    recent, _, calls, _, _, _, _ = recent_ui
    b = recent.b
    b.window = b.main_window
    walk = b.walk
    pending = []
    header = control("ButtonControl", "fixture group", "chat-header__contact-name")

    def remount(root):
        if root is b.main_window and pending:
            if pending.pop() == 1:
                pending.append(0)
            else:
                b.main_window.children.append(header)
        yield from walk(root)

    b.walk = remount
    b.invoke = lambda _: (calls.append("select"), pending.append(1))
    b.open_session("fixture group")
    assert calls == ["select"] and b.header() is header
