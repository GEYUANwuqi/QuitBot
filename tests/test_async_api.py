import asyncio
import threading
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.async_api import AsyncQQInstance
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    InstanceClosed,
    TargetMismatch,
)
from quitbot_backend._engine.instance import QQInstance
from quitbot_backend._engine.models import Message, SendReceipt, Target


class Client:
    def __init__(self, account, *, hwnd=None, targets=None):
        self.owner = threading.get_ident()
        self.backend = SimpleNamespace(
            account=account, hwnd=hwnd or 10, check_thread=self.check_thread
        )
        self.targets = tuple(
            targets or [Target("12345", "测试群"), Target("23456", "friend", "private")]
        )
        self.sent = []
        self.opened = []
        self.frames = 0
        self.block = None
        self.entered = threading.Event()

    def check_thread(self):
        assert threading.get_ident() == self.owner

    def target(self, key):
        return next(
            t
            for t in self.targets
            if t.id == (key.id if isinstance(key, Target) else key)
        )

    def send_text(self, target, text, **kwargs):
        self.check_thread()
        if self.block:
            self.entered.set()
            assert self.block.wait(3)
        self.sent.append((target.id, text))
        return SendReceipt(target.id, str(len(self.sent)), text)

    def open_detached(self, target):
        self.check_thread()
        self.opened.append(target.id)

    def read_detached(self, target):
        self.check_thread()
        self.frames += 1
        return (
            []
            if self.frames == 1
            else [Message("new", target.id, "friend", "ping", "incoming")]
        )


def factory(account, **kwargs):
    return QQInstance.from_client(Client(account, **kwargs))


def test_async_commands_keep_com_affinity_and_loop_responsive():
    async def run():
        qq = await AsyncQQInstance.connect("a", factory=factory, initialize_com=False)
        client = qq._instance._client
        assert client.owner != threading.get_ident()
        client.block = threading.Event()
        send = asyncio.create_task(qq.group("12345").send_text("slow"))
        while not client.entered.is_set():
            await asyncio.sleep(0.001)
        ticks = 0
        for _ in range(3):
            await asyncio.sleep(0.001)
            ticks += 1
        assert ticks == 3 and not send.done()
        client.block.set()
        receipt = await send
        assert receipt.text == "slow"
        await asyncio.gather(*(qq.group("12345").send_text(str(i)) for i in range(5)))
        assert [text for _, text in client.sent] == ["slow", "0", "1", "2", "3", "4"]
        await qq.close()
        with pytest.raises(InstanceClosed):
            await qq.group("12345").send_text("closed")

    asyncio.run(run())


def test_cancel_does_not_replay_running_send_and_close_drains_worker():
    async def run():
        qq = await AsyncQQInstance.connect("a", factory=factory, initialize_com=False)
        client = qq._instance._client
        client.block = threading.Event()
        task = asyncio.create_task(qq.group("12345").send_text("only once"))
        while not client.entered.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        closing = asyncio.create_task(qq.close())
        await asyncio.sleep(0.01)
        assert not closing.done()
        client.block.set()
        await closing
        assert client.sent == [("12345", "only once")]

    asyncio.run(run())


def test_cancelled_queued_send_never_reaches_the_ui():
    async def run():
        qq = await AsyncQQInstance.connect("a", factory=factory, initialize_com=False)
        client = qq._instance._client
        client.block = threading.Event()
        first = asyncio.create_task(qq.group("12345").send_text("first"))
        while not client.entered.is_set():
            await asyncio.sleep(0.001)
        second = asyncio.create_task(qq.group("12345").send_text("cancelled"))
        await asyncio.sleep(0.01)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        client.block.set()
        await first
        await qq.close()
        assert client.sent == [("12345", "first")]

    asyncio.run(run())


def test_detached_events_use_separate_reader_and_support_reply():
    async def run():
        async with await AsyncQQInstance.connect(
            "a", factory=factory, initialize_com=False
        ) as qq:
            events = qq.events(["12345"], duration=1)
            event = await anext(events)
            assert qq._instance._client.opened == ["12345"]
            assert event.message.id == "new"
            assert (await event.reply("pong")).text == "pong"
            qq.stop()
            with pytest.raises(StopAsyncIteration):
                await anext(events)
            client = qq._instance._client
            frames = client.frames
            ready = []

            def failed_open(_):
                raise ControlUnavailable("menu unavailable")

            client.open_detached = failed_open
            with pytest.raises(ControlUnavailable, match="12345.*menu unavailable"):
                await anext(qq.events(["12345"], on_ready=lambda: ready.append(True)))
            assert not ready and client.frames == frames

    asyncio.run(run())


def test_async_ownership_and_kind_are_checked_before_submission():
    async def run():
        async with (
            await AsyncQQInstance.connect(
                "a", factory=factory, initialize_com=False
            ) as a,
            await AsyncQQInstance.connect(
                "b", factory=factory, initialize_com=False
            ) as b,
        ):
            with pytest.raises(TargetMismatch):
                a.conversation(b.group("12345"))
            with pytest.raises(TargetMismatch):
                a.friend("12345")

    asyncio.run(run())
