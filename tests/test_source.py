import asyncio

from quitbot_backend._engine.core.source import MessageSource


def test_message_source_start_stop_are_idempotent_and_health_is_observable():
    async def run():
        delivered = asyncio.Event()
        forever = asyncio.Event()
        received = []

        class QQ:
            calls = 0

            async def events(self, targets, **kwargs):
                self.calls += 1
                kwargs["on_ready"]()
                yield "event"
                await forever.wait()

        qq = QQ()

        async def handler(event):
            received.append(event)
            delivered.set()

        source = MessageSource(qq, ["1"], handler)
        await source.start()
        await source.start()
        await asyncio.wait_for(delivered.wait(), 1)
        assert received == ["event"] and qq.calls == 1
        assert (
            source.health.state == "running"
            and source.health.last_success_at is not None
        )
        await source.stop()
        await source.stop()
        assert source.health.state == "stopped"

    asyncio.run(run())


def test_source_failure_is_reported_without_automatic_resend_or_restart():
    async def run():
        failures = []

        class QQ:
            async def events(self, targets, **kwargs):
                raise RuntimeError("window closed")
                yield  # pragma: no cover

        source = MessageSource(
            QQ(), ["1"], lambda event: None, on_error=failures.append
        )
        await source.start()
        await asyncio.wait_for(source.ready.wait(), 1)
        assert (
            source.health.state == "failed"
            and source.health.last_error == "window closed"
        )
        assert len(failures) == 1
        assert "test_source.py" in source.health.last_error_traceback
        assert "RuntimeError: window closed" in source.health.last_error_traceback
        await source.stop()

    asyncio.run(run())


def test_async_callback_failure_is_a_source_failure_and_cleans_up():
    async def run():
        class QQ:
            async def events(self, targets, **kwargs):
                kwargs["on_ready"]()
                yield "event"

        async def bad(event):
            raise ValueError("handler failed")

        source = MessageSource(QQ(), ["1"], bad)
        await source.start()
        await source._task
        assert (
            source.health.state == "failed"
            and source.health.last_error == "handler failed"
        )
        await source.stop()

    asyncio.run(run())
