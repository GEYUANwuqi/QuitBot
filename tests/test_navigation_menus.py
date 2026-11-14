import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.adapters.onebot.api import OneBotAdapter
from quitbot_backend._engine.domain.controls import MenuAction
from quitbot_backend._engine.errors import ControlUnavailable, TargetMismatch
from quitbot_backend._engine.models import Target
from quitbot_backend._engine.ui.workflows.navigation import NavigationUI


def test_menu_area_allowlists_reject_unknown_requests_before_ui_access():
    ui = NavigationUI(SimpleNamespace(control_ui=object(), backend=object()))
    with pytest.raises(ValueError):
        ui.global_menu("退出账号")
    with pytest.raises(ValueError):
        ui.conversation_menu("12345", "delete")


@pytest.mark.parametrize("fail_read", [False, True])
def test_global_menu_uses_bound_main_window_and_restores_detached_reference(
    monkeypatch, fail_read
):
    import quitbot_backend._engine.ui.workflows.navigation as module

    calls = []
    detached = object()
    main = SimpleNamespace(SetActive=lambda **_: calls.append("activate-main"))
    child = SimpleNamespace(IsKeyboardFocusable=True)
    parent = SimpleNamespace(IsKeyboardFocusable=False)
    backend = SimpleNamespace(
        window=detached,
        check_thread=lambda: None,
        auto=SimpleNamespace(SendKeys=lambda key, **_: calls.append(key)),
        walk=lambda _: [parent, child],
        focus_control=lambda x: calls.append(("focus", x)),
    )
    backend.use_main_window = lambda: setattr(backend, "window", main)

    def dismiss():
        assert backend.window is main
        calls.append("dismiss")

    ui = SimpleNamespace(by_class=lambda *_, **kw: parent, _dismiss_menu=dismiss)
    inspector = NavigationUI(SimpleNamespace(control_ui=ui, backend=backend))
    inspector._menu = lambda: object()
    expected = [MenuAction("退出账号", True, "account_session")]

    def read(_):
        assert backend.window is main
        if fail_read:
            raise ControlUnavailable("provider disappeared")
        return expected

    inspector._read = read
    monkeypatch.setattr(module, "require_unlocked", lambda _: None)
    monkeypatch.setattr(module, "ui_transaction", nullcontext)
    if fail_read:
        with pytest.raises(ControlUnavailable):
            inspector.global_menu("more")
    else:
        assert inspector.global_menu("more") == expected
    assert backend.window is detached
    assert calls == ["activate-main", ("focus", child), "{Enter}", "dismiss"]


def test_recent_menu_rejects_duplicate_names_before_context_activation():
    calls = []
    pane = object()
    row1 = SimpleNamespace(ClassName="recent-contact-item")
    row2 = SimpleNamespace(ClassName="recent-contact-item")
    backend = SimpleNamespace(
        window=object(),
        use_main_window=lambda: calls.append("main"),
        open_session=lambda name: calls.append(name),
        profile=lambda *args: calls.append(args),
        find=lambda *_: pane,
        contact_name=lambda _: "测试群",
        walk=lambda root: (
            [row1, row2]
            if root is pane
            else [SimpleNamespace(ControlTypeName="TextControl", Name="测试群")]
        ),
    )
    ui = SimpleNamespace(_context=lambda _: calls.append("context"))
    inspector = NavigationUI(SimpleNamespace(control_ui=ui, backend=backend))
    with pytest.raises(TargetMismatch):
        inspector._recent(Target("12345", "测试群"))
    assert calls == ["main", "测试群", ("12345", "测试群", "group")]


def test_onebot_menu_read_routes_global_and_configured_conversation_without_commits():
    calls = []

    class Owner:
        async def menus(self, area):
            calls.append((self, area))
            return [MenuAction("删除", True, "destructive")]

    qq = Owner()
    qq.account = "test"
    scope = Owner()
    qq.conversation = lambda key: (
        scope if key == "12345" else (_ for _ in ()).throw(ValueError("unknown target"))
    )

    async def run():
        adapter = OneBotAdapter(qq, 23456)
        for params in ({"area": "more"}, {"area": "recent", "target_id": "12345"}):
            response = await adapter.handle({"action": "x_get_menu", "params": params})
            assert (
                response["retcode"] == 0
                and response["data"][0]["risk"] == "destructive"
            )
        bad = await adapter.handle(
            {"action": "x_get_menu", "params": {"area": "recent", "target_id": "54321"}}
        )
        assert bad["retcode"] == 1400
        bad = await adapter.handle({"action": "delete_msg", "params": {}})
        assert bad["retcode"] == 1400
        adapter.index.close()

    asyncio.run(run())
    assert calls == [(qq, "more"), (scope, "recent")]
