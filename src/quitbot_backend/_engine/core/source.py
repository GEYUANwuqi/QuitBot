"""A small observable lifecycle for a message source; no bot business logic."""

import asyncio
import inspect
import time
import traceback
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceHealth:
    state: str
    last_success_at: float | None
    last_error: str | None
    last_error_traceback: str | None = None


class MessageSource:
    def __init__(
        self,
        qq,
        targets,
        on_message,
        *,
        on_error=None,
        ready=None,
        mode="detached",
        interval=1.0,
        refresh_detached=False,
    ):
        if type(refresh_detached) is not bool or (
            refresh_detached and mode != "detached"
        ):
            raise ValueError("refresh_detached requires detached mode and a boolean")
        self.qq, self.targets = qq, tuple(targets)
        self.on_message, self.on_error = on_message, on_error
        self.mode, self.interval = mode, interval
        self.refresh_detached = refresh_detached
        self.ready = ready if ready is not None else asyncio.Event()
        self._task = None
        self._state = "stopped"
        self._last_success = self._last_error = None
        self._last_traceback = None

    @property
    def health(self):
        return SourceHealth(
            self._state, self._last_success, self._last_error, self._last_traceback
        )

    async def start(self):
        if self._task is not None and not self._task.done():
            return self
        self.ready.clear()
        self._state = "starting"
        self._last_error = None
        self._last_traceback = None
        self._task = asyncio.create_task(self._run())
        return self

    def _on_ready(self):
        self._state = "running"
        self._last_success = time.time()
        self.ready.set()

    async def _invoke(self, callback, value):
        result = callback(value)
        if inspect.isawaitable(result):
            await result

    async def _run(self):
        try:
            async for event in self._events():
                await self._invoke(self.on_message, event)
                self._last_success = time.time()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - UI source failures are observable health state
            self._state, self._last_error = "failed", str(error)
            # Local diagnostics only: no locals captured or traceback broadcast.
            self._last_traceback = "".join(traceback.format_exception(error))[-12000:]
            if self.on_error is not None:
                try:
                    await self._invoke(self.on_error, error)
                except Exception as handler_error:  # noqa: BLE001 - retain both failures
                    self._last_error += f"; error handler failed: {handler_error}"
        else:
            self._state = "stopped"
        finally:
            self.ready.set()

    def _events(self):
        return self.qq.events(
            self.targets,
            mode=self.mode,
            interval=self.interval,
            on_ready=self._on_ready,
            refresh_detached=self.refresh_detached,
        )

    async def stop(self):
        if self._task is not None:
            self._state = "stopping"
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._state = "stopped"


class ActivitySource(MessageSource):
    def __init__(self, *args, kinds=("group", "friend"), track_roster=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.kinds, self.track_roster = kinds, track_roster

    def _events(self):
        return self.qq.activities(
            self.targets,
            kinds=self.kinds,
            mode=self.mode,
            interval=self.interval,
            on_ready=self._on_ready,
            track_roster=self.track_roster,
            refresh_detached=self.refresh_detached,
        )
