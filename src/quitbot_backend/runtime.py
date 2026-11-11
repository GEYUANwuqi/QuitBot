"""One account, one action queue and one event stream. No transport dependency."""

import asyncio
import logging
import time
from pathlib import Path

from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from ._engine.adapters.onebot.api import OneBotAdapter
from ._engine.async_api import AsyncQQInstance
from ._engine.core.source import ActivitySource, MessageSource
from ._engine.domain.group_files import GroupFileEntry
from ._engine.domain.onebot import Unsupported
from ._engine.errors import MutationOutcomeUnknown, QuitBotError, SendOutcomeUnknown
from ._engine.infrastructure.storage.message_index import MessageIndex
from ._engine.models import Target
from .config import AccountConfig
from .contracts import ACTIONS, ReverseWSStatus, RuntimeStatus, validate_result

log = logging.getLogger(__name__)


class AccountRuntime:
    def __init__(
        self, config: AccountConfig, state_dir: Path, *, connect=AsyncQQInstance.connect
    ):
        self.config = config
        self.targets = tuple(Target(t.id, t.name, t.kind) for t in config.targets)
        self.state_dir, self._connect = state_dir, connect
        self.qq = self.adapter = self.index = None
        self.sources = []
        self.last_error = None
        self.catalog = {}
        self.reverse_ws = ReverseWSStatus(
            enabled=config.reverse_ws.enabled,
            url=config.reverse_ws.url,
            role=config.reverse_ws.role,
            state="stopped" if config.reverse_ws.enabled else "disabled",
        )
        self._lock = asyncio.Lock()
        self._jobs = set()
        self._subscribers = set()
        self._closing = False

    def status(self) -> dict:
        states = [s.health.state for s in self.sources]
        state = (
            "failed"
            if "failed" in states
            else "starting"
            if "starting" in states
            else "running"
            if "running" in states
            else "stopped"
        )
        return RuntimeStatus(
            account_id=self.config.account_id,
            label=self.config.label,
            connected=self.qq is not None,
            listening=state == "running",
            mode=self.config.mode,
            targets=list(self.targets),
            last_error=self.last_error,
            listener_state=state,
            reverse_ws=self.reverse_ws,
        ).model_dump(mode="json")

    def event(self, detail_type: str, **data):
        return {
            "time": int(time.time()),
            "self_id": int(self.config.account_id),
            "post_type": "quitbot",
            "detail_type": detail_type,
            **data,
        }

    def subscribe(self):
        queue = asyncio.Queue(128)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue):
        self._subscribers.discard(queue)

    def publish(self, event):
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self.unsubscribe(queue)
                # Explicitly end a slow consumer; never silently lose events.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)

    async def _bind(self):
        if self.qq is not None:
            return
        qq = await self._connect(
            self.config.account_id, hwnd=self.config.hwnd, targets=self.targets
        )
        try:
            # Native connect already reads the identity on its dedicated UIA thread.
            identity = getattr(qq, "verified_id", None)
            if identity is None:
                identity = await qq.identify()
            if str(identity) != self.config.account_id:
                raise ValueError("Window identity differs from configured QQ account")
            index = MessageIndex(
                self.state_dir / self.config.account_id / "messages.sqlite3"
            )
            bindings = {
                (b.group_id, b.name): b.user_id for b in self.config.member_bindings
            }
            self.adapter = OneBotAdapter(
                qq, self.config.account_id, index=index, member_ids=bindings
            )
            self.qq, self.index = qq, index
            self.last_error = None
        except BaseException:
            await qq.close()
            raise

    async def _stop_listener(self):
        for source in self.sources:
            await source.stop()
        self.sources.clear()

    async def _disconnect(self):
        await self._stop_listener()
        if self.qq is not None:
            await self.qq.close()
        if self.index is not None:
            self.index.close()
        self.qq = self.adapter = self.index = None

    async def _start_listener(self):
        if not self.targets:
            raise ValueError(
                "请先在账号设置的「监听范围」中添加群聊或私聊，保存后再启动监听"
            )
        if self.sources and all(
            s.health.state in ("starting", "running") for s in self.sources
        ):
            return
        await self._stop_listener()
        self.last_error = None
        target_ids = [t.id for t in self.targets]
        if self.config.resolve_members:
            await self.adapter.resolve_members(target_ids)
        options = {
            "mode": self.config.mode,
            "interval": self.config.interval,
            "refresh_detached": self.config.refresh_detached,
            "on_error": self._source_error,
        }
        self.sources = [MessageSource(self.qq, target_ids, self._message, **options)]
        if self.config.activities:
            self.sources.append(
                ActivitySource(
                    self.qq,
                    target_ids,
                    self._activity,
                    kinds=self.config.request_kinds,
                    track_roster=self.config.track_roster,
                    **options,
                )
            )
        for source in self.sources:
            await source.start()

    async def _message(self, event):
        if (
            self.config.resolve_members
            and event.conversation.target.kind == "group"
            and event.message.sender
            and self.adapter._user_id(event.conversation.target, event.message) is None
        ):
            try:
                await self.adapter.resolve_sender(
                    event.conversation, event.message.sender
                )
            except (QuitBotError, ValueError):
                pass
        self.publish(
            self.adapter.message_event(event.conversation.target, event.message)
        )

    def _activity(self, observation):
        if observation["kind"] == "request_card":
            event = self.adapter.notification_event(observation["card"])
        elif observation["kind"] == "system_message":
            target, message = observation["target"], observation["message"]
            event = self.adapter.notice_event(target, message) or self.event(
                "system_observation",
                target_id=target.id,
                kind=target.kind,
                ui_message_id=message.id,
                raw_text=message.text,
            )
        else:
            event = self.event("loaded_roster_change", **jsonable_encoder(observation))
        if event is not None:
            self.publish(event)

    def _source_error(self, error):
        self.last_error = str(error)
        self.publish(self.event("listener_error", message=str(error)))

    async def handle(self, value) -> dict:
        """Retain running mutations if a network caller disconnects. Never retry."""
        echo = (
            {"echo": value["echo"]}
            if isinstance(value, dict) and "echo" in value
            else {}
        )
        if self._closing or len(self._jobs) >= 64:
            return {
                "status": "failed",
                "retcode": 100,
                "data": None,
                "message": "Service closing or action queue full",
                **echo,
            }
        task = asyncio.create_task(self._handle(value, echo))
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)
        return await asyncio.shield(task)

    async def _handle(self, value, echo):
        try:
            if not isinstance(value, dict) or set(value) - {"action", "params", "echo"}:
                raise ValueError("Use {action, params, echo?}")
            name = value.get("action")
            if not isinstance(name, str):
                raise TypeError("action must be a string")
            if name not in ACTIONS:
                raise Unsupported("Unknown action: " + name)
            action = ACTIONS[name]
            params = action.params.model_validate(value.get("params", {})).model_dump(
                mode="json", exclude_unset=True, exclude_none=True
            )
            async with self._lock:
                if name == "x_get_runtime_status":
                    data = self.status()
                elif name == "x_get_api_catalog":
                    data = self.catalog
                elif name in ("x_get_capabilities", "x_get_capability_matrix"):
                    data = {
                        "actions": self.catalog.get("actions", []),
                        "transport": "UIA",
                        "partial": True,
                        "scope": "configured targets and loaded GUI state",
                    }
                elif name == "x_connect":
                    await self._bind()
                    data = self.status()
                elif name == "x_disconnect":
                    await self._disconnect()
                    data = self.status()
                else:
                    if self.qq is None:
                        raise RuntimeError(
                            "Account is disconnected; call x_connect first"
                        )
                    if name in ("x_start_listener", "x_stop_listener"):
                        await (
                            self._start_listener()
                            if name == "x_start_listener"
                            else self._stop_listener()
                        )
                        data = self.status()
                    elif action.dispatch == "native":
                        data = await self._extension(name, params)
                    else:
                        response = await self.adapter.handle(
                            {"action": name, "params": params}
                        )
                        if response["status"] != "ok":
                            return {**response, **echo}
                        data = response["data"]
                try:
                    data = validate_result(action, jsonable_encoder(data))
                except (ValidationError, TypeError, ValueError) as error:
                    log.exception("Invalid result contract for %s", name)
                    if action.mutating:
                        raise MutationOutcomeUnknown(
                            "Result contract failed after execution"
                        ) from error
                    raise RuntimeError(
                        "Backend result does not match the declared schema"
                    ) from error
            return {"status": "ok", "retcode": 0, "data": data, **echo}
        except Unsupported as error:
            code, data, message = 1404, None, str(error)
        except (SendOutcomeUnknown, MutationOutcomeUnknown):
            code, data, message = (
                101,
                {"retryable": False, "outcome": "unknown"},
                "Operation outcome unknown; inspect QQ before retrying",
            )
        except (ValueError, TypeError, KeyError) as error:
            code, data, message = 1400, None, str(error)
        except (QuitBotError, OSError, RuntimeError) as error:
            code, data, message = 100, None, str(error)
        except Exception:
            log.exception("Unexpected action failure")
            code, data, message = (
                100,
                None,
                "Internal action failure; inspect server logs",
            )
        self.last_error = message
        return {
            "status": "failed",
            "retcode": code,
            "data": data,
            "message": message,
            "wording": message,
            **echo,
        }

    async def _extension(self, name, p):
        if name == "x_discover_conversations":
            return await self.qq.discover_conversations()
        if name == "x_resolve_conversation":
            return await self.qq.resolve_conversation(p["name"])
        scope = self.qq.conversation(str(p["target_id"]))
        expected = p.get("expected")
        if name in ("x_delete_group_files", "x_download_group_files"):
            entries = (
                {k: GroupFileEntry(**v) for k, v in expected.items()}
                if expected is not None
                else None
            )
            if name == "x_delete_group_files":
                return await scope.delete_files(p["names"], expected=entries)
            return await scope.download_files(
                p["names"], p["destination"], expected=entries
            )
        if name == "x_download_group_folder":
            return await scope.download_folder(
                p["name"],
                p["download_root"],
                expected=GroupFileEntry(**expected) if expected else None,
            )
        if name == "x_get_group_folder_properties":
            return await scope.folder_properties(
                p["name"], expected=GroupFileEntry(**expected) if expected else None
            )
        if name == "x_save_group_qr":
            return await scope.save_qr(p["destination"])
        if name == "x_share_group_qr":
            return await scope.share_qr_to(str(p["destination"]))
        if name == "x_share_contact":
            return await scope.share_to(str(p["destination"]))
        if name == "x_get_friend_profile":
            return await scope.profile(full=p.get("full", False))
        if name == "x_like_friend":
            return await scope.like()
        if name == "x_get_contact_card_profile":
            return await scope.contact_profile(
                p["ui_message_id"], expected_user_id=str(p["expected_user_id"])
            )
        if name == "x_get_favorite_stickers":
            return await scope.favorite_stickers()
        if name == "x_import_stickers":
            return await scope.import_stickers(p["paths"])
        if name == "x_move_sticker_first":
            return await scope.move_sticker_first(p["selector"])
        if name == "x_get_sticker_remark":
            return await scope.sticker_remark(p["selector"])
        if name == "x_set_sticker_remark":
            return await scope.set_sticker_remark(p["selector"], p["value"])
        if name == "x_delete_sticker":
            return await scope.delete_sticker(p["selector"])
        if name == "x_get_history_calendar":
            return await scope.history_calendar(p["year"], p["month"])
        if name == "x_locate_history_image":
            return await scope.locate_history_image(
                p["on_date"], expected_message_id=p["expected_message_id"]
            )
        if name == "x_get_messages":
            return [
                {
                    "message_id": self.adapter.message_event(scope.target, message)[
                        "message_id"
                    ],
                    "message": message,
                }
                for message in await scope.messages()
            ]
        raise Unsupported(name)

    async def close(self):
        self._closing = True
        if self._jobs:
            await asyncio.gather(*tuple(self._jobs), return_exceptions=True)
        async with self._lock:
            await self._disconnect()
        for queue in tuple(self._subscribers):
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(None)
        self._subscribers.clear()
