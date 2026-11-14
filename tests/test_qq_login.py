import asyncio
import threading
from types import SimpleNamespace

import pytest
from auth_helpers import authenticated
from fastapi.testclient import TestClient

from quitbot_backend.auth import AccessKeys
from quitbot_backend.config import ConfigStore
from quitbot_backend.discovery import DiscoveredProcess
from quitbot_backend.management import create_management_app
from quitbot_backend.qq_login import (
    QQLoginAction,
    QQLoginManager,
    QQLoginRequest,
    QQLoginStart,
)
from quitbot_backend.service import Service


class Driver:
    def __init__(self):
        self.calls = []
        self.closed = False

    def start(self, hwnd, pid, *, new_instance=False):
        self.calls.append(("start", hwnd, pid, new_instance, threading.get_ident()))
        return {"state": "qr", "qr_image": "data:image/png;base64,c3ludGhldGlj"}

    def read(self):
        self.calls.append(("read", threading.get_ident()))
        return {"state": "expired"}

    def act(self, action, account):
        self.calls.append((action, account, threading.get_ident()))
        return {"state": "waiting"}

    def close(self):
        self.closed = True


def test_login_requires_changed_password_on_http_and_ws(tmp_path):
    access = AccessKeys(tmp_path / "security.json")
    driver = Driver()
    service = Service(
        ConfigStore(tmp_path / "accounts.json"),
        access,
        login_driver_factory=lambda: driver,
    )
    with TestClient(create_management_app(service)) as client:
        assert client.post("/api/qq/login/start", json={}).status_code == 401
        token = access.login(access.initial_password).access_token
        assert (
            client.post(
                "/api/qq/login/start",
                json={},
                headers={"Authorization": f"Bearer {token}"},
            ).status_code
            == 403
        )
        assert driver.calls == []
        token = authenticated(access)
        response = client.post(
            "/api/qq/login/start", json={}, headers={"Authorization": f"Bearer {token}"}
        )
        assert (
            response.status_code == 200
            and response.headers["Cache-Control"] == "no-store"
        )
        state = response.json()
        with client.websocket_connect(
            "/ws", headers={"Authorization": f"Bearer {token}"}
        ) as ws:
            ws.send_json(
                {
                    "action": "x_get_qq_login",
                    "params": {"login_id": state["login_id"]},
                    "echo": 7,
                }
            )
            data = ws.receive_json()
            assert data["echo"] == 7 and data["data"]["state"] == "expired"
            assert data["data"]["qr_image"] is None
            ws.send_json(
                {
                    "action": "x_submit_qq_login",
                    "params": {
                        "login_id": state["login_id"],
                        "action": "arbitrary_command",
                    },
                }
            )
            assert ws.receive_json()["status"] == "failed"
        assert len({c[-1] for c in driver.calls}) == 1
        assert (
            client.post(
                "/api/qq/login/start",
                json={"executable": "/tmp/other"},
                headers={"Authorization": f"Bearer {token}"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/qq/login/start",
                json={"hwnd": 101, "pid": 201, "new_instance": True},
                headers={"Authorization": f"Bearer {token}"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/qq/login/start",
                json={"new_instance": True},
                headers={"Authorization": f"Bearer {token}"},
            ).status_code
            == 200
        )
        assert driver.calls[-1][3] is True
    asyncio.run(service.close())
    assert driver.closed


def test_login_cancel_does_not_interrupt_action_and_close_drains():
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        driver = Driver()

        def act(action, account):
            entered.set()
            assert release.wait(5)
            driver.calls.append(("completed", threading.get_ident()))
            return {"state": "waiting"}

        driver.act = act
        manager = QQLoginManager(lambda: driver)
        view = await manager.call("start", QQLoginStart())
        request = QQLoginAction(login_id=view.login_id, action="qr")
        task = asyncio.create_task(manager.call("act", request))
        await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        closing = asyncio.create_task(manager.close())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await closing
        assert driver.closed and driver.calls[-1][0] == "completed"

    asyncio.run(scenario())


def test_old_login_id_rejected_after_new_flow():
    async def scenario():
        manager = QQLoginManager(Driver)
        try:
            old = await manager.call("start", QQLoginStart())
            new = await manager.call("start", QQLoginStart())
            assert old.login_id != new.login_id
            with pytest.raises(ValueError, match="已结束"):
                await manager.call("read", QQLoginRequest(login_id=old.login_id))
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_login_window_replacement_stays_with_original_process(monkeypatch):
    from quitbot_backend._engine.ui.workflows import login

    driver = login.QQLoginDriver()
    driver.pid = 77
    driver.window = DiscoveredProcess(hwnd=11, pid=77)
    windows = [DiscoveredProcess(hwnd=12, pid=88)]
    monkeypatch.setattr(login, "scan_windows", lambda: windows)
    assert driver._window() is None
    assert driver._window() is None
    windows.append(DiscoveredProcess(hwnd=13, pid=77))
    assert driver._window().hwnd == 13


def test_saved_accounts_allow_nicknames_but_reject_ambiguous_selection():
    from quitbot_backend._engine.ui.workflows.login import QQLoginDriver

    def control(cls, name="", role="GroupControl", children=()):
        node = SimpleNamespace(
            ClassName=cls, Name=name, ControlTypeName=role, children=children
        )
        node.GetChildren = lambda: node.children
        return node

    name = control("avatar-name", children=[control("", "测试账号", "TextControl")])
    avatar = control("avatar-item is-selected", "测试账号", "ButtonControl", [name])

    def walk(node):
        yield node
        for child in node.children:
            yield from walk(child)

    auto = SimpleNamespace(
        WalkControl=lambda root, **_: [(c, 1) for c in list(walk(root))[1:]]
    )
    driver = QQLoginDriver()
    result = driver._accounts(auto, [avatar])
    assert result[0][1]["account_id"] is None
    assert result[0][1]["nickname"] == "测试账号"
    assert result[0][1]["selected"]
    name.children[0].Name = "12345"
    avatar.Name = "12345"
    assert driver._accounts(auto, [avatar])[0][1]["account_id"] is None
    with pytest.raises(ValueError, match="同名"):
        driver._accounts(auto, [avatar, avatar])


def test_activation_rechecks_the_account_shown_after_login(tmp_path):
    from quitbot_backend.discovery import DiscoveredWindow

    async def scenario():
        access = AccessKeys(tmp_path / "security.json")
        window = DiscoveredProcess(hwnd=101, pid=201)
        service = Service(
            ConfigStore(tmp_path / "accounts.json"),
            access,
            discover=lambda: [window],
            resolve_profile=lambda _: DiscoveredWindow(
                hwnd=101,
                pid=201,
                nickname="测试账号B",
                account_id="10002",
                avatar="data:image/png;base64,c3ludGhldGlj",
            ),
        )
        try:
            await service.refresh_discovery()
            revision = service.store.revision
            with pytest.raises(ValueError, match="号码不同"):
                await service.activate_account(101, revision, "10001")
            assert service.store.revision == revision
            assert service.store.current.accounts == []
        finally:
            await service.close()

    asyncio.run(scenario())


def test_launch_coalesces_startup_and_can_reopen_hidden_owned_client(monkeypatch):
    from quitbot_backend._engine.platforms import launch
    from quitbot_backend._engine.ui.workflows import login

    windows, processes = [], []

    class Process:
        def __init__(self):
            self.terminated = False

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, **_):
            return 0

    def spawn(*args, **kwargs):
        process = Process()
        processes.append(process)
        return process

    monkeypatch.setattr(login, "scan_windows", lambda: list(windows))
    monkeypatch.setattr(launch, "qq_command", lambda: ["fixture-qq"])
    monkeypatch.setattr(login.subprocess, "Popen", spawn)
    driver = login.QQLoginDriver()
    driver.read = lambda: {"state": "starting"}
    driver.start(None, None)
    driver.start(None, None)
    assert len(processes) == 1
    windows.append(DiscoveredProcess(hwnd=101, pid=201))
    driver.start(None, None)
    driver.profile = object()
    driver.submitted_at = login.time.monotonic()
    driver.start(None, None)
    assert driver.profile is None
    windows.clear()
    driver.start(None, None)
    assert len(processes) == 2
    driver.close()
    assert all(p.terminated for p in processes)


def test_open_existing_allows_explicit_qq_process_handoff(monkeypatch):
    from quitbot_backend._engine.ui.workflows import login

    driver = login.QQLoginDriver()
    driver.window = DiscoveredProcess(hwnd=101, pid=201)
    driver.pid = 201
    windows = [driver.window]
    button = SimpleNamespace(
        ClassName="", ControlTypeName="ButtonControl", Name="打开QQ"
    )
    root = SimpleNamespace(ProcessId=201, SetActive=lambda **_: None)
    auto = SimpleNamespace(ControlFromHandle=lambda _: root)
    monkeypatch.setattr(login, "scan_windows", lambda: windows)
    monkeypatch.setattr(login, "automation", lambda: auto)
    monkeypatch.setattr(login, "require_unlocked", lambda _: None)
    driver._walk = lambda *_: [button]
    actions = []
    driver._invoke = lambda _, controls: actions.append(controls)
    assert driver._operate()["state"] == "existing"
    assert driver._operate("open_existing")["state"] == "waiting"
    assert actions == [[button]]
    windows[:] = [DiscoveredProcess(hwnd=102, pid=202)]
    assert driver._window().pid == 202


def test_new_login_tracks_only_its_window_and_keeps_existing_accounts(monkeypatch):
    from quitbot_backend._engine.platforms import launch
    from quitbot_backend._engine.ui.workflows import login

    first = DiscoveredProcess(hwnd=101, pid=201)
    second = DiscoveredProcess(hwnd=102, pid=202)
    windows, processes = [first], []

    class Process:
        def __init__(self, pid):
            self.pid = pid
            self.terminated = False

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, **_):
            return 0

    def spawn(*_, **__):
        process = Process(202 + len(processes))
        processes.append(process)
        return process

    monkeypatch.setattr(login, "scan_windows", lambda: list(windows))
    monkeypatch.setattr(launch, "qq_command", lambda: ["fixture-qq"])
    monkeypatch.setattr(login.subprocess, "Popen", spawn)
    driver = login.QQLoginDriver()
    driver.window, driver.pid, driver.profile = first, first.pid, object()

    def read():
        window = driver._window()
        return {"state": "accounts" if window else "starting"}

    driver.read = read
    assert driver.start(None, None, new_instance=True)["state"] == "starting"
    assert driver.window is None and driver.profile is None
    windows[0] = DiscoveredProcess(hwnd=111, pid=first.pid)
    assert driver._window() is None  # A pre-existing account may replace its HWND.
    windows[0] = first
    driver.start(None, None, new_instance=True)
    assert len(processes) == 1
    windows.append(second)
    assert driver.start(None, None, new_instance=True)["state"] == "accounts"
    assert driver.window == second
    assert len(processes) == 1
    windows.remove(second)
    assert driver._window() is None
    assert driver._window() is None  # Never fall back to the first account.
    windows.append(second)
    assert driver._window() == second
    driver.profile = object()  # Second account has finished logging in.
    assert driver.start(None, None, new_instance=True)["state"] == "starting"
    assert len(processes) == 2 and windows == [first, second]
    third = DiscoveredProcess(hwnd=103, pid=203)
    windows.append(third)
    assert driver._window() == third

    def completed_while_panel_closed():
        if driver._window() is None:
            return {"state": "starting"}
        driver.profile = object()
        return {"state": "logged_in"}

    driver.read = completed_while_panel_closed
    assert driver.profile is None
    assert driver.start(None, None, new_instance=True)["state"] == "starting"
    assert len(processes) == 3  # A completed but unpolled login is not reused.
    driver.read = read
    driver.start(first.hwnd, first.pid)
    assert driver.window == first and len(processes) == 3
    driver.close()
    assert all(p.terminated for p in processes)


def test_single_account_selector_and_selection_only_qr_entry(monkeypatch):
    from quitbot_backend._engine.ui.workflows import login

    actions = []

    def control(
        cls, name="", role="GroupControl", children=(), action=None, select=False
    ):
        pattern = (
            SimpleNamespace(Select=action) if select else SimpleNamespace(Invoke=action)
        )
        node = SimpleNamespace(
            ClassName=cls,
            Name=name,
            ControlTypeName=role,
            children=list(children),
            IsEnabled=True,
            IsOffscreen=False,
            GetPattern=lambda kind: (
                pattern
                if action and kind == ("select" if select else "invoke")
                else None
            ),
            GetLegacyIAccessiblePattern=lambda: None,
        )
        node.GetChildren = lambda: node.children
        return node

    def text(name):
        return control("", name, "TextControl")

    def toggle(**_):
        actions.append("selector")
        if menu in name.children:
            name.children.remove(menu)
        else:
            name.children.append(menu)

    name = control("name", children=[text("测试账号")], action=toggle)
    single = control("single-login", children=[name])
    row = control(
        "account-list-item",
        "测试账号 10001",
        "MenuItemControl",
        [
            control("name", children=[text("测试账号")]),
            control("uin", children=[text("10001")]),
        ],
    )

    def open_password(**_):
        actions.append("add")
        root.children = [control("password-container"), scan]

    def open_qr(**_):
        actions.append("qr")
        root.children = [control("qr-code-img")]

    add = control(
        "add-user", "添加账号", "MenuItemControl", action=open_password, select=True
    )
    menu = control(
        "account-list show-scroll-bar show-name",
        role="MenuControl",
        children=[row, add],
    )
    scan = control("", "扫码登录", "ButtonControl", action=open_qr)
    login_button = control(
        "login-btn", "登录", "ButtonControl", action=lambda **_: actions.append("login")
    )
    root = control("", children=[single, login_button])
    root.ProcessId, root.SetActive = 201, lambda **_: None

    def walk(node):
        yield node
        for child in node.children:
            yield from walk(child)

    auto = SimpleNamespace(
        PatternId=SimpleNamespace(
            InvokePattern="invoke", SelectionItemPattern="select"
        ),
        ControlFromHandle=lambda _: root,
        WalkControl=lambda node, **_: [(c, 1) for c in list(walk(node))[1:]],
    )
    window = DiscoveredProcess(hwnd=101, pid=201)
    monkeypatch.setattr(login, "scan_windows", lambda: [window])
    monkeypatch.setattr(login, "automation", lambda: auto)
    monkeypatch.setattr(login, "require_unlocked", lambda _: None)
    driver = login.QQLoginDriver()
    driver.window, driver.pid = window, window.pid
    view = driver._operate()
    assert view["state"] == "accounts"
    account = view["accounts"][0]
    assert account["nickname"] == "测试账号" and account["account_id"] == "10001"
    assert account["selected"]
    assert driver._operate("login", account["key"])["state"] == "waiting"
    assert actions[-2:] == ["selector", "login"] and driver.expected_account == "10001"
    driver.submitted_at = None
    assert driver._operate("qr")["state"] == "waiting"
    assert actions[-3:] == ["selector", "add", "qr"]
    assert root.children[0].ClassName == "qr-code-img"
