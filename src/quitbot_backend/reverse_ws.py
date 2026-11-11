"""Per-account outbound OneBot WebSocket with reconnects and no request replay."""

import asyncio
import json
import logging
import time

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from .runtime import AccountRuntime

log = logging.getLogger(__name__)


class ReverseWebSocket:
    def __init__(self, runtime: AccountRuntime):
        self.runtime = runtime
        self.config = runtime.config.reverse_ws
        self.status = runtime.reverse_ws
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self.config.enabled and self._task is None:
            self.status.state = "connecting"
            self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.status.state = "stopped" if self.config.enabled else "disabled"

    async def _run(self) -> None:
        headers = {
            "X-Self-ID": self.runtime.config.account_id,
            "X-Client-Role": self.config.role,
        }
        if self.config.access_token:
            headers["Authorization"] = "Bearer " + self.config.access_token
        while True:
            try:
                async with connect(
                    self.config.url,
                    additional_headers=headers,
                    proxy=None,
                    open_timeout=10,
                    close_timeout=2,
                    max_size=1024 * 1024,
                    max_queue=16,
                ) as socket:
                    self.status.state = "connected"
                    self.status.last_error = None
                    await self._session(socket)
                    message = f"连接已关闭（{socket.close_code}）"
            except InvalidStatus as error:
                message = f"握手失败（HTTP {error.response.status_code}）"
            except ConnectionClosed as error:
                message = (
                    f"连接已关闭（{error.rcvd.code if error.rcvd else '未收到关闭帧'}）"
                )
            except (OSError, TimeoutError, WebSocketException) as error:
                # Exception text may contain a URL, headers or the receiver's body.
                message = f"连接失败（{type(error).__name__}）"
            self.status.state = "reconnecting"
            if self.status.last_error != message:
                log.warning("Reverse WS account %s: %s", headers["X-Self-ID"], message)
            self.status.last_error = message
            await asyncio.sleep(self.config.reconnect_interval)

    async def _session(self, socket: ClientConnection) -> None:
        events = self.config.role != "API"
        api = self.config.role != "Event"
        queue = self.runtime.subscribe() if events else None
        send_lock = asyncio.Lock()
        tasks = []

        async def send(value):
            async with send_lock:
                await socket.send(json.dumps(value, ensure_ascii=False))

        async def publish():
            while True:
                event = await queue.get()
                if event is None:
                    await socket.close(
                        1013, "Event queue overflow; no replay available"
                    )
                    return
                await send(event)

        async def receive():
            async for raw in socket:
                if not isinstance(raw, str):
                    await socket.close(1003, "Use JSON text frames")
                    return
                if not api:
                    continue
                try:
                    request = json.loads(raw)
                except (ValueError, RecursionError):
                    await send(
                        {
                            "status": "failed",
                            "retcode": 1400,
                            "data": None,
                            "message": "Invalid JSON",
                        }
                    )
                    continue
                await send(await self.runtime.handle(request))

        try:
            if events:
                await send(
                    {
                        "time": int(time.time()),
                        "self_id": int(self.runtime.config.account_id),
                        "post_type": "meta_event",
                        "meta_event_type": "lifecycle",
                        "sub_type": "connect",
                    }
                )
                tasks.append(asyncio.create_task(publish()))
            tasks.append(asyncio.create_task(receive()))
            # Observe disconnects even while receive() awaits a native operation.
            tasks.append(asyncio.create_task(socket.wait_closed()))
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            if queue is not None:
                self.runtime.unsubscribe(queue)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
