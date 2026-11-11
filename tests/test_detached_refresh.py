from quitbot_backend.config import AccountConfig

"""Explicit foreground refresh never weakens detached-window identity checks."""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.async_api import AsyncQQInstance
from quitbot_backend._engine.client import QQClient
from quitbot_backend._engine.errors import ControlUnavailable, TargetMismatch
from quitbot_backend._engine.instance import QQInstance
from quitbot_backend._engine.models import Message, Target
from quitbot_backend._engine.uia import DetachedWindow, UIABackend

TARGET = Target("12345", "测试群")


def refresh_backend(monkeypatch):
    from quitbot_backend._engine.infrastructure.uia import inspection

    calls = []
    state = {"foreground": None, "header": TARGET.name, "activate": True}
    window = SimpleNamespace(NativeWindowHandle=100, ProcessId=10)

    def activate(**_):
        calls.append("activate")
        if state["activate"]:
            state["foreground"] = window

    window.SetActive = activate
    backend = UIABackend.__new__(UIABackend)
    backend.main_window = SimpleNamespace(ProcessId=10)
    backend.window = window
    backend._detached = {TARGET.id: DetachedWindow(TARGET.id, TARGET.name, 100, 10)}
    backend.check_thread = lambda: None
    backend.chat_windows = lambda _: [window]
    backend.header = lambda: SimpleNamespace(Name=state["header"])
    backend.auto = SimpleNamespace(GetForegroundControl=lambda: state["foreground"])
    monkeypatch.setattr(
        inspection, "require_unlocked", lambda _: calls.append("unlock")
    )

    def wait(read, **_):
        if not read():
            raise ControlUnavailable("Foreground did not match")

    monkeypatch.setattr("quitbot_backend._engine.uia.wait_until", wait)
    return backend, calls, state


def test_refresh_activates_exact_verified_window_once(monkeypatch):
    backend, calls, state = refresh_backend(monkeypatch)
    backend.refresh_detached(TARGET)
    assert calls == ["unlock", "activate"]
    assert state["foreground"].NativeWindowHandle == 100


@pytest.mark.parametrize("mismatch", ["handle", "process", "header", "unopened"])
def test_refresh_rejects_changed_or_missing_binding_before_input(monkeypatch, mismatch):
    backend, calls, state = refresh_backend(monkeypatch)
    if mismatch == "handle":
        backend.window.NativeWindowHandle = 200
    elif mismatch == "process":
        backend.window.ProcessId = 20
    elif mismatch == "header":
        state["header"] = "different"
    else:
        backend._detached.clear()
    with pytest.raises((ControlUnavailable, TargetMismatch)):
        backend.refresh_detached(TARGET)
    assert "activate" not in calls


def test_refresh_never_replays_failed_activation(monkeypatch):
    backend, calls, state = refresh_backend(monkeypatch)
    state["activate"] = False
    with pytest.raises(ControlUnavailable):
        backend.refresh_detached(TARGET)
    assert calls.count("activate") == 1


def test_ambiguous_detached_error_preserves_guard_and_identifies_matching_windows():
    backend = UIABackend.__new__(UIABackend)
    backend._detached = {}
    backend.chat_windows = lambda _: [
        SimpleNamespace(NativeWindowHandle=100, ProcessId=10, Name="测试群"),
        SimpleNamespace(NativeWindowHandle=200, ProcessId=10, Name="测试群"),
    ]
    with pytest.raises(TargetMismatch) as error:
        backend.activate_detached(TARGET)
    assert all(text in str(error.value) for text in (TARGET.id, "100", "200", "测试群"))


def test_refresh_checks_locked_desktop_before_activation(monkeypatch):
    from quitbot_backend._engine.infrastructure.uia import inspection

    backend, calls, _ = refresh_backend(monkeypatch)

    def locked(_):
        raise inspection.DesktopLocked("locked")

    monkeypatch.setattr(inspection, "require_unlocked", locked)
    with pytest.raises(inspection.DesktopLocked):
        backend.refresh_detached(TARGET)
    assert "activate" not in calls


def test_client_default_does_not_refresh_and_opt_in_stays_inside_transaction(
    monkeypatch,
):
    calls = []

    @contextmanager
    def transaction():
        calls.append("lock")
        yield
        calls.append("release")

    monkeypatch.setattr("quitbot_backend._engine.client.ui_transaction", transaction)
    client = QQClient.__new__(QQClient)
    client.targets = (TARGET,)
    client.backend = SimpleNamespace(
        _detached={TARGET.id: object()},
        check_thread=lambda: None,
        activate_detached=lambda *_args, **_kwargs: calls.append("bind"),
        refresh_detached=lambda _: calls.append("refresh"),
    )
    client._read = lambda _: calls.append("read") or []
    assert client.read_detached(TARGET) == []
    assert calls == ["lock", "bind", "read", "release"]
    calls.clear()
    assert client.read_detached(TARGET, refresh=True) == []
    assert calls == ["lock", "bind", "refresh", "read", "release"]


def test_sync_events_and_direct_reads_propagate_explicit_refresh():
    client = QQClient.__new__(QQClient)
    client.targets = (TARGET,)
    client.backend = SimpleNamespace(check_thread=lambda: None)
    reads = []
    client.open_detached = lambda _: None
    message = Message("new", TARGET.id, "peer", "test", "incoming")

    def read(_target, **options):
        reads.append(options)
        return [] if len(reads) == 1 else [message]

    client.read_detached = read
    instance = QQInstance.from_client(client)
    events = instance.events([TARGET.id], mode="detached", refresh_detached=True)
    try:
        assert next(events).message.data == message
        assert reads == [{"refresh": True}, {"refresh": True}]
        assert (
            instance.conversation(TARGET.id).detached_messages(refresh=True)[0].data
            == message
        )
        assert reads[-1] == {"refresh": True}
    finally:
        events.close()


@pytest.mark.parametrize("stream", ["events", "activities"])
@pytest.mark.parametrize("refresh", [False, True])
def test_async_streams_propagate_opt_in_to_each_detached_read(stream, refresh):
    async def run():
        qq = AsyncQQInstance(initialize_com=False)
        calls = []

        async def opened():
            return None

        qq.targets = (TARGET,)
        qq.group(TARGET.id).open_detached = opened

        async def read(_target, method, **options):
            calls.append((method, options))
            return (
                []
                if len(calls) == 1
                else [
                    Message(
                        "new",
                        TARGET.id,
                        None,
                        "test",
                        "incoming" if stream == "events" else "system",
                    )
                ]
            )

        qq._call = read
        kwargs = {"kinds": ()} if stream == "activities" else {}
        iterator = getattr(qq, stream)(
            [TARGET.id], refresh_detached=refresh, interval=0.2, **kwargs
        )
        try:
            await asyncio.wait_for(anext(iterator), 2)
            expected = {"refresh": True} if refresh else {}
            assert calls == [("detached_messages", expected)] * 2
        finally:
            await iterator.aclose()
            await qq.close()

    asyncio.run(run())




def test_switch_mode_rejects_refresh_before_native_work():
    client = QQClient.__new__(QQClient)
    with pytest.raises(ValueError, match="refresh_detached"):
        next(client.watch([], mode="switch", refresh_detached=True))
    with pytest.raises(ValueError, match="refresh_detached"):
        AccountConfig(account_id="23456", mode="switch", refresh_detached=True)

    async def run():
        qq = AsyncQQInstance(initialize_com=False)
        try:
            for method in (qq.events, qq.activities):
                iterator = method([], mode="switch", refresh_detached=True)
                with pytest.raises(ValueError, match="refresh_detached"):
                    await anext(iterator)
        finally:
            await qq.close()

    asyncio.run(run())
