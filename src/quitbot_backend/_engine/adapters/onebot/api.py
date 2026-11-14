"""Bounded OneBot application adapter; no native selectors."""

import asyncio
import base64
import mimetypes
import re
import sqlite3
import time
from collections import OrderedDict
from dataclasses import asdict
from importlib.metadata import version
from urllib.parse import unquote, urlparse

from quitbot_backend._engine.core.capabilities import CAPABILITIES
from quitbot_backend._engine.domain.activities import (
    group_notice,
    private_poke_notice,
    recall_actor,
)
from quitbot_backend._engine.domain.group_files import GroupFileEntry
from quitbot_backend._engine.domain.notices import GroupNotice
from quitbot_backend._engine.domain.onebot import (
    ApiRequest,
    ApiResponse,
    MessageSegment,
    OneBotMessageEvent,
    Unsupported,
    cq_escape,
    numeric_id,
    parse_segments,
)
from quitbot_backend._engine.errors import (
    MutationOutcomeUnknown,
    QuitBotError,
    SendOutcomeUnknown,
)
from quitbot_backend._engine.infrastructure.storage.media import ImageCache
from quitbot_backend._engine.infrastructure.storage.message_index import MessageIndex
from quitbot_backend._engine.operations import validate_text
from quitbot_backend._engine.platforms import TRANSPORT_NAME

SECONDARY_ACTIONS = (
    "x_get_group_applications",
    "x_get_member_submenu",
    "x_get_group_files",
    "x_get_group_notices",
    "x_publish_group_notice",
    "x_edit_group_notice",
    "x_delete_group_notice",
    "x_get_group_notice_pinned",
    "x_set_group_notice_pinned",
    "x_get_group_essences",
    "x_get_group_join_policy",
    "x_set_group_join_mode",
    "x_set_group_member_invites",
    "x_set_group_invite_approval",
    "x_set_group_bot_invites",
    "x_set_group_bot_invite_approval",
    "x_get_group_description",
    "x_set_group_description",
    "x_get_group_tags",
    "x_get_group_share_link",
    "x_set_group_tag",
    "x_get_group_file_entries",
    "x_create_group_folder",
    "x_rename_group_folder",
    "x_delete_group_folder",
    "x_upload_group_file",
    "x_rename_group_file",
    "x_move_group_file",
    "x_delete_group_file",
    "x_download_group_file",
    "x_get_group_file_actions",
    "x_get_group_file_sort_options",
    "x_get_group_file_sort_state",
    "x_get_message_secondary",
    "x_get_reaction_palette",
)


class OneBotAdapter:
    def __init__(self, qq, self_id, *, index=None, member_ids=None, image_cache=None):
        self.qq = qq
        self.self_id = numeric_id(self_id)
        self.index = index or MessageIndex()
        # Explicit (group_id, exact display name) -> QQ number; never infer IDs
        # from nicknames or transfer a mapping across groups.
        self.member_ids = {
            tuple(k): numeric_id(v) for k, v in (member_ids or {}).items()
        }
        self._ui_member_ids = {}
        self._request_flags = {}
        self._message_actors = OrderedDict()
        self.image_cache = image_cache
        self._images = OrderedDict()
        self._faces = OrderedDict()

    def _remember_actor(self, target, ui_id, user_id):
        key = (target.kind, target.id, str(ui_id))
        self._message_actors[key] = user_id
        self._message_actors.move_to_end(key)
        while len(self._message_actors) > 4096:
            self._message_actors.popitem(last=False)

    def notice_event(self, target, message):
        bindings = {
            name: number
            for (group, name), number in self.member_ids.items()
            if group == target.id
        }
        actor = recall_actor(message, target, self.self_id, bindings)
        if actor is not None:
            # Only correlate a recall to an original message whose sender was
            # actually resolved in this adapter, not a similar-looking nickname.
            if self._message_actors.get((target.kind, target.id, message.id)) != actor:
                return None
            event = {
                "time": int(time.time()),
                "self_id": self.self_id,
                "post_type": "notice",
                "notice_type": "group_recall"
                if target.kind == "group"
                else "friend_recall",
                "user_id": actor,
                "message_id": self.index.register(self.self_id, target, message.id),
                "quitbot": {
                    "ui_message_id": message.id,
                    "raw_text": message.text,
                    "time_source": "observed",
                    "message_id_source": "local_uia_mapping",
                },
            }
            if target.kind == "group":
                event.update(group_id=numeric_id(target.id), operator_id=actor)
            return event
        return (
            group_notice(message, self.self_id, bindings)
            if target.kind == "group"
            else private_poke_notice(message, target, self.self_id)
        )

    def notification_event(self, card):
        if card.status != "pending":
            return None
        targets = getattr(self.qq, "targets", ())
        if card.kind == "friend":
            peers = [
                t for t in targets if t.kind == "private" and t.name == card.actor_name
            ]
            if len(peers) != 1:
                return None
            number, group = numeric_id(peers[0].id), None
        else:
            groups = [
                t for t in targets if t.kind == "group" and t.name == card.group_name
            ]
            if len(groups) != 1:
                return None
            group = numeric_id(groups[0].id)
            number = self.member_ids.get((str(group), card.actor_name))
            if number is None or card.summary != "申请加入":
                return None
        self._request_flags[card.ui_key] = card
        event = {
            "time": int(time.time()),
            "self_id": self.self_id,
            "post_type": "request",
            "request_type": card.kind,
            "user_id": number,
            "comment": card.comment,
            "flag": card.ui_key,
            "quitbot": {
                "identity_source": "explicit_mapping",
                "time_source": "observed",
                "flag_source": "local_uia_card",
                "time_text": card.time_text,
            },
        }
        if group is not None:
            event.update(group_id=group, sub_type="add")
        return event

    async def resolve_members(self, targets):
        result = {}
        for key in targets:
            scope = self.qq.conversation(key)
            if scope.target.kind != "group":
                continue
            result[scope.id] = await self._resolved_members(scope)
        return result

    async def resolve_sender(self, scope, name):
        profile = await scope.member_profile(name)
        key = (scope.id, name)
        number = numeric_id(profile.user_id)
        if key in self.member_ids and self.member_ids[key] != number:
            raise ValueError("Explicit member mapping conflicts with the QQ profile")
        self._ui_member_ids[key] = (number, time.monotonic() + 60)
        return profile

    async def _bound_member_name(self, scope, user_id):
        number = numeric_id(user_id)
        candidates = {
            name
            for (group, name), value in self.member_ids.items()
            if group == scope.id and value == number
        }
        candidates.update(
            name
            for (group, name), (value, expires) in self._ui_member_ids.items()
            if group == scope.id and value == number and expires > time.monotonic()
        )
        candidates.update(
            t.name
            for t in getattr(self.qq, "targets", ())
            if t.kind == "private" and numeric_id(t.id) == number
        )
        members = await scope.members()
        names = [m.name for m in members]
        candidates = {name for name in candidates if names.count(name) == 1}
        if len(candidates) != 1:
            raise Unsupported(
                "Resolve the member's UI profile or configure an explicit member mapping first"
            )
        return candidates.pop()

    async def _resolved_members(self, scope):
        members = await scope.members(resolve_ids=True)
        names = [member.name for member in members]
        for key in tuple(self._ui_member_ids):
            if key[0] == scope.id:
                del self._ui_member_ids[key]
        for member in members:
            if member.user_id is not None and names.count(member.name) == 1:
                key = (scope.id, member.name)
                number = numeric_id(member.user_id)
                if key in self.member_ids and self.member_ids[key] != number:
                    raise ValueError(
                        "Explicit member mapping conflicts with the QQ profile"
                    )
                self._ui_member_ids[key] = (number, time.monotonic() + 60)
        return members

    def _forget_member_binding(self, group_id, number):
        for key, bound in tuple(self.member_ids.items()):
            if key[0] == group_id and bound == number:
                del self.member_ids[key]
        for key, (bound, _) in tuple(self._ui_member_ids.items()):
            if key[0] == group_id and bound == number:
                del self._ui_member_ids[key]

    async def _set_member_card(self, scope, name, value, number):
        binding = self.member_ids.get((scope.id, value))
        if binding is not None and binding != number:
            raise ValueError("Requested card conflicts with an explicit member mapping")
        try:
            receipt = await scope.set_member_card(
                name, value, expected_user_id=str(number)
            )
        except MutationOutcomeUnknown:
            self._forget_member_binding(scope.id, number)
            raise
        self._forget_member_binding(scope.id, number)
        self._ui_member_ids[(scope.id, receipt.after_name)] = (
            number,
            time.monotonic() + 60,
        )
        return receipt

    def _user_id(self, target, message):
        if message.direction == "outgoing":
            return self.self_id
        if target.kind == "private":
            return numeric_id(target.id)
        key = (target.id, message.sender)
        if key in self.member_ids:
            return self.member_ids[key]
        binding = self._ui_member_ids.get(key)
        return (
            binding[0]
            if binding is not None and binding[1] > time.monotonic()
            else None
        )

    async def prepare_message(self, scope, message):
        key = (scope.target.kind, scope.target.id, message.id)
        if message.face_count and key not in self._faces:
            try:
                faces = await scope.export_face_ids(message.id)
                if len(faces) == message.face_count:
                    self._faces[key] = faces
                    self._faces.move_to_end(key)
                    while len(self._faces) > 1024:
                        self._faces.popitem(last=False)
            except (QuitBotError, OSError, ValueError):
                pass
        if not self.image_cache or not message.attachments or any(a.kind != "image" for a in message.attachments):
            return
        key = (scope.target.kind, scope.target.id, message.id)
        cached = self._images.get(key)
        if cached:
            try:
                for name in cached:
                    self.image_cache.get(name)
                return
            except ValueError:
                self._images.pop(key, None)
        try:
            images = await scope.export_images(message.id)
            if len(images) != len(message.attachments):
                return
            names = await asyncio.to_thread(lambda: [self.image_cache.put(data) for data in images])
        except (QuitBotError, OSError, ValueError):
            return
        self._images[key] = names
        self._images.move_to_end(key)
        while len(self._images) > 1024:
            self._images.popitem(last=False)

    def message_event(self, target, message):
        message_id = self.index.register(self.self_id, target, message.id)
        user_id = self._user_id(target, message)
        if user_id is not None and message.direction != "system":
            self._remember_actor(target, message.id, user_id)
        observed = int(time.time())
        metadata = {
            "ui_message_id": message.id,
            "time_source": "observed",
            "timestamp_text": message.timestamp,
        }
        if target.kind == "group" and user_id is not None:
            metadata["sender_id_source"] = (
                "explicit_mapping"
                if (target.id, message.sender) in self.member_ids
                else "uia_profile_name_match"
                if message.direction != "outgoing"
                else "self"
            )
        images = self._images.get((target.kind, target.id, message.id), [])
        if images:
            try:
                for name in images:
                    self.image_cache.get(name)
            except (OSError, ValueError):
                images = []
        faces = self._faces.get((target.kind, target.id, message.id), [])
        face_parts = sum(p.kind == "face" for p in message.parts)
        faces_ready = bool(faces) and len(faces) == message.face_count == face_parts
        face_index = 0
        image_parts = sum(p.kind == "image" for p in message.parts)
        media_ready = bool(images) and len(images) == len(message.attachments) and (
            image_parts == len(images) or not message.parts and not message.text and len(images) == 1
        )
        parts = []
        raw_parts = []
        image_index = 0
        def append_image(name):
            data = {"file": name, "url": self.image_cache.get(name).as_uri()}
            parts.append(asdict(MessageSegment("image", data)))
            raw_parts.append(f"[CQ:image,file={name},url={cq_escape(data['url']).replace(',', '&#44;')}]")
        for part in message.parts:
            if part.kind == "text":
                parts.append(asdict(MessageSegment("text", {"text": part.text})))
                raw_parts.append(cq_escape(part.text))
            elif part.kind == "face":
                if faces_ready:
                    number = faces[face_index]
                    parts.append(asdict(MessageSegment("face", {"id": str(number)})))
                    raw_parts.append(f"[CQ:face,id={number}]")
                    face_index += 1
            elif part.kind == "image":
                if media_ready:
                    append_image(images[image_index])
                    image_index += 1
            else:
                number = (
                    "all"
                    if part.text == "全体成员"
                    else self.member_ids.get((target.id, part.text))
                )
                if number is None:
                    binding = self._ui_member_ids.get((target.id, part.text))
                    number = (
                        binding[0]
                        if binding and binding[1] > time.monotonic()
                        else None
                    )
                if number is None:
                    metadata["unresolved_mention"] = part.text
                    break
                parts.append(asdict(MessageSegment("at", {"qq": str(number)})))
                raw_parts.append(f"[CQ:at,qq={number}]")
        if media_ready and not message.parts:
            append_image(images[0])
        if (
            user_id is None
            or (message.attachments and not media_ready)
            or (message.face_count and not faces_ready)
            or message.kind not in ("text", "mixed", "image", "face")
            or message.kind == "face" and not faces_ready
            or message.kind == "image" and not media_ready
            or "unresolved_mention" in metadata
        ):
            # UI-only media and unresolved senders cannot honestly be represented
            # by OB11's required numeric user_id/image file fields.
            return {
                "time": observed,
                "self_id": self.self_id,
                "post_type": "quitbot",
                "detail_type": "unresolved_message",
                "message_id": message_id,
                "reason": "sender_id_unavailable"
                if user_id is None
                else "mention_id_unavailable"
                if "unresolved_mention" in metadata
                else "forward_id_unavailable"
                if message.kind == "forward"
                else "random_sticker_details_unavailable"
                if message.kind == "random_sticker"
                else "card_payload_unavailable"
                if message.kind == "card"
                else "face_id_unavailable"
                if message.face_count
                else "media_not_exportable",
                "target": asdict(target),
                "ui_message": asdict(message),
                "_quitbot": metadata,
            }
        value = OneBotMessageEvent(
            observed,
            self.self_id,
            "message",
            target.kind,
            "normal" if target.kind == "group" else "friend",
            message_id,
            user_id,
            parts or [asdict(MessageSegment("text", {"text": message.text}))],
            "".join(raw_parts) if parts else cq_escape(message.text),
            0,
            {"user_id": user_id, "nickname": message.sender or ""},
            numeric_id(target.id) if target.kind == "group" else None,
        ).to_dict()
        if message.quote:
            metadata["quote"] = asdict(message.quote)
        value["_quitbot"] = metadata
        return value

    async def handle(self, value):
        request = (
            ApiRequest("", {}, value.get("echo"), "echo" in value)
            if isinstance(value, dict)
            else None
        )
        try:
            request = ApiRequest.parse(value)
            data = await self._action(request.action, request.params)
            response = ApiResponse("ok", 0, data)
        except Unsupported as error:
            response = ApiResponse("failed", 1404, message=str(error))
        except (ValueError, TypeError, KeyError) as error:
            response = ApiResponse("failed", 1400, message=str(error))
        except (SendOutcomeUnknown, MutationOutcomeUnknown):
            response = ApiResponse(
                "failed",
                101,
                {"retryable": False, "outcome": "unknown"},
                "Mutation outcome unknown; inspect QQ before retrying",
            )
        except (QuitBotError, OSError, RuntimeError) as error:
            response = ApiResponse("failed", 100, message=str(error))
        return response.to_dict(request)

    def _scope(self, kind, number):
        scope = self.qq.conversation(str(numeric_id(number)))
        if scope.target.kind != kind:
            raise ValueError("Target kind mismatch")
        return scope

    def _sent_id(self, scope, receipt):
        try:
            number = self.index.register(self.self_id, scope.target, receipt.message_id)
            self._remember_actor(scope.target, receipt.message_id, self.self_id)
            return {"message_id": number}
        except (sqlite3.Error, OSError, OverflowError) as error:
            raise SendOutcomeUnknown(
                "Message was submitted but its protocol ID could not be persisted"
            ) from error

    async def _send(self, scope, params):
        auto_escape = params.get("auto_escape", False)
        if not isinstance(auto_escape, bool):
            raise TypeError("auto_escape must be boolean")
        segments = parse_segments(params["message"], auto_escape=auto_escape)
        # Validate the whole message before any UI action: no partial sends.
        if not segments:
            raise ValueError("Empty message")
        if len(segments) == 1 and segments[0].type == "image":
            data = segments[0].data
            if (
                set(data) - {"file", "url"}
                or not isinstance(data.get("file"), str)
                or "url" in data and not isinstance(data["url"], str)
            ):
                raise Unsupported("Only one local image file is supported")
            path = data["file"]
            if self.image_cache and ImageCache.valid_name(path):
                path = str(self.image_cache.get(path))
            if path.startswith("file://"):
                parsed = urlparse(path)
                if parsed.netloc not in ("", "localhost"):
                    raise Unsupported("Network file URI is unsupported")
                path = (
                    unquote(parsed.path).lstrip("/")
                    if re.match(r"/[A-Za-z]:", parsed.path)
                    else unquote(parsed.path)
                )
            elif "://" in path or path.startswith("base64://"):
                raise Unsupported("Remote/base64 media is unsupported")
            receipt = await scope.send_image(path)
        elif segments[0].type == "at":
            if scope.target.kind != "group" or set(segments[0].data) != {"qq"}:
                raise Unsupported("Native at requires one group member QQ number")
            member = str(numeric_id(segments[0].data["qq"]))
            rest = segments[1:]
            if not rest or any(
                s.type != "text"
                or set(s.data) != {"text"}
                or not isinstance(s.data["text"], str)
                for s in rest
            ):
                raise Unsupported("Supported mention shape: one at followed by text")
            text = "".join(s.data["text"] for s in rest)
            validate_text(text)
            receipt = await scope.send_mention(member, text)
            if hasattr(receipt, "member_id") and hasattr(receipt, "member_name"):
                self._ui_member_ids[(scope.id, receipt.member_name)] = (
                    numeric_id(receipt.member_id),
                    time.monotonic() + 60,
                )
        else:
            reply_id = None
            if segments[0].type == "reply":
                if set(segments[0].data) != {"id"}:
                    raise ValueError("reply requires only id")
                kind, target, reply_id = self.index.resolve(
                    self.self_id, segments[0].data["id"]
                )
                if (kind, target) != (scope.target.kind, scope.id):
                    raise ValueError("Reply message belongs to another conversation")
                segments = segments[1:]
            if not segments or any(
                s.type != "text"
                or set(s.data) != {"text"}
                or not isinstance(s.data["text"], str)
                for s in segments
            ):
                raise Unsupported(
                    "Supported shapes: text, reply+text, or a single local image"
                )
            text = "".join(s.data["text"] for s in segments)
            validate_text(text)
            receipt = (
                await scope.reply(reply_id, text)
                if reply_id is not None
                else await scope.send_text(text)
            )
        return self._sent_id(scope, receipt)

    async def _action(self, action, params):
        if action in ("send_group_msg", "send_private_msg", "send_msg"):
            kind = (
                "group"
                if action == "send_group_msg"
                else "private"
                if action == "send_private_msg"
                else params.get("message_type")
            )
            if kind is None:
                kind = "group" if "group_id" in params else "private"
            if kind not in ("group", "private"):
                raise ValueError("Invalid message_type")
            return await self._send(
                self._scope(kind, params["group_id" if kind == "group" else "user_id"]),
                params,
            )
        if action == "get_login_info":
            return {"user_id": self.self_id, "nickname": self.qq.account}
        if action == "get_version_info":
            return {
                "app_name": "QuitBot",
                "app_version": version("quitbot"),
                "protocol_version": "v11",
                "_quitbot": {"partial": True, "transport": TRANSPORT_NAME},
            }
        if action == "x_get_client_version":
            return {"client_name": "QQ", "client_version": await self.qq.client_version(), "source": "atspi_about" if TRANSPORT_NAME == "AT-SPI" else "uia_about"}
        if action == "x_get_storage_paths":
            return await self.qq.storage_paths()
        if action == "get_msg":
            kind, number, ui_id = self.index.resolve(self.self_id, params["message_id"])
            scope = self._scope(kind, number)
            message = await scope.get_message(ui_id)
            await self.prepare_message(scope, message)
            event = self.message_event(scope.target, message)
            if event["post_type"] != "message":
                raise Unsupported(
                    "Required sender/media data unavailable; use x_get_ui_message"
                )
            return {
                "time": event["time"],
                "message_type": kind,
                "message_id": event["message_id"],
                "sender": event["sender"],
                "message": event["message"],
                "_quitbot": event["_quitbot"],
            }
        if action == "x_download_message_file":
            kind, number, ui_id = self.index.resolve(self.self_id, params["message_id"])
            scope = self._scope(kind, number)
            return await scope.download_message_file(ui_id, params["destination"], expected_name=params["expected_name"])
        if action in ("get_image", "x_get_image"):
            if self.image_cache is None:
                raise Unsupported("Image cache is unavailable")
            path = self.image_cache.get(params["file"])
            if action == "get_image":
                return {"file": str(path)}
            data = await asyncio.to_thread(path.read_bytes)
            return {"file": path.name, "base64": base64.b64encode(data).decode("ascii"),
                    "size": len(data), "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream"}
        if action == "can_send_image":
            return {"yes": True}
        if action == "can_send_record":
            return {"yes": False}
        if action == "get_group_info":
            info = await self._scope("group", params["group_id"]).info()
            return {
                "group_id": numeric_id(info.id),
                "group_name": info.name,
                "_quitbot": {"fields": info.fields, "partial": True},
            }
        if action in ("get_group_member_info", "get_group_member_list"):
            scope = self._scope("group", params["group_id"])
            members = await self._resolved_members(scope)
            rows = [
                {
                    "group_id": numeric_id(scope.id),
                    "user_id": numeric_id(m.user_id),
                    "nickname": m.nickname or "",
                    "card": m.name,
                    "role": m.role,
                    "_quitbot": {"scope": "loaded_ui", "complete": False},
                }
                for m in members
                if m.user_id is not None
            ]
            if action == "get_group_member_list":
                return rows
            matches = [
                row for row in rows if row["user_id"] == numeric_id(params["user_id"])
            ]
            if len(matches) != 1:
                raise Unsupported("Member is not uniquely present in the loaded UI")
            return matches[0]
        if action == "group_poke":
            scope = self._scope("group", params["group_id"])
            number = numeric_id(params["user_id"])
            name = await self._bound_member_name(scope, number)
            await scope.poke_member(name, expected_user_id=str(number))
            return None
        if action == "x_poke_member":
            return asdict(
                await self._scope("group", params["target_id"]).poke_member(
                    params["member_name"], expected_user_id=params["expected_user_id"]
                )
            )
        if action == "x_poke_friend":
            return asdict(await self._scope("private", params["target_id"]).poke())
        if action == "set_group_card":
            number = numeric_id(params.get("user_id", self.self_id))
            value = params.get("card", "")
            scope = self._scope("group", params["group_id"])
            if number == self.self_id:
                await scope.set_own_card(value)
            else:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("Other-member card requires a nonempty value")
                name = await self._bound_member_name(scope, number)
                await self._set_member_card(scope, name, value, number)
            return None
        if action == "x_set_member_card":
            scope = self._scope("group", params["target_id"])
            return asdict(
                await self._set_member_card(
                    scope,
                    params["member_name"],
                    params["value"],
                    numeric_id(params["expected_user_id"]),
                )
            )
        if action == "set_group_whole_ban":
            await self._scope("group", params["group_id"]).set_whole_ban(
                params.get("enable", True)
            )
            return None
        if action == "x_get_group_preferences":
            return asdict(await self._scope("group", params["target_id"]).preferences())
        if action in ("x_get_reactions", "x_add_reaction", "x_remove_own_reaction"):
            kind, number, ui_id = self.index.resolve(self.self_id, params["message_id"])
            if kind != "group":
                raise ValueError("Native reactions require a group message")
            scope = self._scope(kind, number)
            if action == "x_get_reactions":
                result = await scope.reactions(ui_id)
            elif action == "x_add_reaction":
                result = await scope.add_reaction(
                    ui_id, params["label"], expected_text=params.get("expected_text")
                )
            else:
                result = await scope.remove_own_reaction(
                    ui_id, expected_text=params.get("expected_text")
                )
            return asdict(result)
        if action in (
            "delete_msg",
            "set_essence_msg",
            "delete_essence_msg",
            "x_get_message_essence",
        ):
            kind, number, ui_id = self.index.resolve(self.self_id, params["message_id"])
            scope = self._scope(kind, number)
            if action == "delete_msg":
                await scope.recall(ui_id, expected_text=params.get("expected_text"))
                return None
            if kind != "group":
                raise ValueError("Essence actions require a group message")
            if action == "x_get_message_essence":
                return {"enabled": await scope.essence_state(ui_id)}
            await scope.set_essence(ui_id, action == "set_essence_msg")
            return None
        if action == "x_set_muted":
            return asdict(
                await self.qq.conversation(str(params["target_id"])).set_muted(
                    params["enabled"]
                )
            )
        if action == "x_get_auto_download":
            return {
                "enabled": await self._scope(
                    "private", params["target_id"]
                ).auto_download()
            }
        if action == "x_set_auto_download":
            return asdict(
                await self._scope("private", params["target_id"]).set_auto_download(
                    params["enabled"]
                )
            )
        if action == "x_set_group_speak_limit":
            return asdict(
                await self._scope("group", params["target_id"]).set_speak_limit(
                    params["per_minute"]
                )
            )
        if action == "x_set_group_message_mode":
            return asdict(
                await self._scope("group", params["target_id"]).set_message_mode(
                    params["mode"]
                )
            )
        if action in ("forward_group_single_msg", "forward_friend_single_msg"):
            kind, target_id, ui_id = self.index.resolve(
                self.self_id, params["message_id"]
            )
            destination = self._scope(
                "group" if action == "forward_group_single_msg" else "private",
                params[
                    "group_id" if action == "forward_group_single_msg" else "user_id"
                ],
            )
            receipt = await self._scope(kind, target_id).forward(ui_id, destination)
            return self._sent_id(destination, receipt)
        if action == "x_get_menu":
            owner = (
                self.qq
                if "target_id" not in params
                else self.qq.conversation(str(params["target_id"]))
            )
            return [asdict(row) for row in await owner.menus(params["area"])]
        if action == "x_set_blocked":
            scope = self._scope("private", params["target_id"])
            return asdict(await scope.set_blocked(params["enabled"]))
        if action == "x_set_member_ban":
            scope = self._scope("group", params["target_id"])
            return asdict(
                await scope.set_member_ban(params["member_name"], params["duration"])
            )
        if action == "set_group_ban":
            duration = params.get("duration", 1800)
            if type(duration) is not int:
                raise ValueError("duration must be an integer")
            if not 0 <= duration < 30 * 86400 or duration % 60:
                raise Unsupported(
                    "Native duration requires whole minutes below 30 days, or 0 to unmute"
                )
            scope = self._scope("group", params["group_id"])
            number = numeric_id(params["user_id"])
            name = await self._bound_member_name(scope, number)
            profile = await scope.member_profile(name)
            if numeric_id(profile.user_id) != number:
                raise ValueError(
                    "Member QQ profile does not match the requested user_id"
                )
            await scope.set_member_ban(name, duration)
            return None
        if action == "set_group_admin":
            enabled = params.get("enable", True)
            if type(enabled) is not bool:
                raise ValueError("enable must be boolean")
            scope = self._scope("group", params["group_id"])
            number = numeric_id(params["user_id"])
            name = await self._bound_member_name(scope, number)
            await scope.set_member_admin(name, enabled, expected_user_id=str(number))
            return None
        if action == "x_set_member_admin":
            scope = self._scope("group", params["target_id"])
            return asdict(
                await scope.set_member_admin(
                    params["member_name"],
                    params["enabled"],
                    expected_user_id=params["expected_user_id"],
                )
            )
        if action == "x_kick_member":
            scope = self._scope("group", params["target_id"])
            return asdict(
                await scope.kick_member(
                    params["member_name"], expected_user_id=params["expected_user_id"]
                )
            )
        if action == "set_group_kick":
            reject = params.get("reject_add_request", False)
            if not isinstance(reject, bool):
                raise ValueError("reject_add_request must be boolean")
            if reject:
                raise Unsupported("Blocking future group applications is unsupported")
            scope = self._scope("group", params["group_id"])
            number = numeric_id(params["user_id"])
            name = await self._bound_member_name(scope, number)
            return asdict(await scope.kick_member(name, expected_user_id=str(number)))
        if action == "set_group_leave":
            if params.get("is_dismiss", False) is not False:
                raise Unsupported("Group dissolution is unsupported")
            return asdict(await self._scope("group", params["group_id"]).leave())
        if action == "x_get_notifications":
            cards = await self.qq.notifications(params.get("kind", "group"))
            for card in cards:
                self.notification_event(card)
            return [asdict(card) for card in cards]
        if action in ("set_friend_add_request", "set_group_add_request"):
            card = self._request_flags.get(params["flag"])
            kind = "friend" if action == "set_friend_add_request" else "group"
            if card is None or card.kind != kind:
                raise ValueError(
                    "Unknown request flag for this instance; observe the request first"
                )
            if params.get("remark") or params.get("reason"):
                raise Unsupported(
                    "Request remarks/rejection reasons are not implemented"
                )
            if kind == "group" and params.get("sub_type", "add") != "add":
                raise Unsupported("Only observed group applications are supported")
            result = await self.qq.respond_request(
                kind, card.ui_key, approve=params.get("approve", True)
            )
            self._request_flags.pop(card.ui_key, None)
            return result
        if action == "x_mark_unread":
            return asdict(
                await self.qq.conversation(str(params["target_id"])).mark_unread()
            )
        if action == "x_send_face":
            scope = self.qq.conversation(str(params["target_id"]))
            receipt = await scope.send_face(
                params["label"], text=params.get("text", "")
            )
            result = self._sent_id(scope, receipt)
            result["_quitbot"] = asdict(receipt)
            return result
        if action in SECONDARY_ACTIONS:
            scope = self.qq.conversation(str(params["target_id"]))
            if action in ("x_get_message_secondary", "x_get_reaction_palette"):
                kind, number, ui_id = self.index.resolve(
                    self.self_id, params["message_id"]
                )
                if (kind, number) != (scope.target.kind, scope.id):
                    raise ValueError("Message belongs to another conversation")
                value = await (
                    scope.message_secondary(ui_id)
                    if action == "x_get_message_secondary"
                    else scope.reaction_palette(ui_id)
                )
                return asdict(value)
            if scope.target.kind != "group":
                raise ValueError("This secondary view requires a group")
            if action == "x_get_group_applications":
                return [asdict(row) for row in await scope.application_menu()]
            if action == "x_get_member_submenu":
                return asdict(
                    await scope.member_submenu(params["member"], params["entry"])
                )
            if action == "x_get_group_files":
                options = {"query": params.get("query")}
                if "order" in params:
                    options["order"] = params["order"]
                return asdict(await scope.files(**options))
            if action == "x_get_group_notices":
                return asdict(await scope.notices())
            if action == "x_publish_group_notice":
                options = {"image": params["image"]} if "image" in params else {}
                return asdict(await scope.publish_notice(params["text"], **options))
            if action in ("x_edit_group_notice", "x_delete_group_notice", "x_get_group_notice_pinned", "x_set_group_notice_pinned"):
                options = {"expected": GroupNotice(**params["expected"])} if params.get("expected") is not None else {}
                if action == "x_get_group_notice_pinned":
                    return {"pinned": await scope.notice_pinned(params["text"], **options)}
                if action == "x_set_group_notice_pinned":
                    return asdict(await scope.set_notice_pinned(params["text"], params["enabled"], **options))
                if action == "x_edit_group_notice":
                    options.update({key: params[key] for key in ("image", "remove_image") if key in params})
                    return asdict(await scope.edit_notice(params["text"], params["new_text"], **options))
                return asdict(await scope.delete_notice(params["text"], **options))
            if action == "x_get_group_essences":
                return asdict(await scope.essences())
            if action == "x_get_group_join_policy":
                return asdict(await scope.join_policy())
            if action == "x_get_group_description":
                return {"description": await scope.description()}
            if action == "x_get_group_tags":
                return {"labels": list(await scope.tags())}
            if action == "x_get_group_share_link":
                return {"url": await scope.share_link()}
            if action == "x_set_group_tag":
                return asdict(await scope.set_tag(params["label"], params["enabled"]))
            if action == "x_set_group_description":
                return asdict(await scope.set_description(params["value"]))
            policy_fields = {
                "x_set_group_" + field: "set_" + field
                for field in (
                    "join_mode",
                    "member_invites",
                    "invite_approval",
                    "bot_invites",
                    "bot_invite_approval",
                )
            }
            if action in policy_fields:
                return asdict(
                    await getattr(scope, policy_fields[action])(params["value"])
                )
            if action == "x_get_group_file_entries":
                return asdict(await scope.file_entries(folder=params.get("folder")))
            if action == "x_create_group_folder":
                return asdict(await scope.create_folder(params["name"]))
            if action == "x_upload_group_file":
                return asdict(await scope.upload_file(params["path"]))
            if action in (
                "x_rename_group_folder",
                "x_delete_group_folder",
                "x_rename_group_file",
                "x_move_group_file",
                "x_delete_group_file",
                "x_download_group_file",
            ):
                expected = params.get("expected")
                if expected is not None:
                    if not isinstance(expected, dict):
                        raise ValueError("expected must be a typed file entry object")
                    expected = GroupFileEntry(**expected)
                if action == "x_rename_group_folder":
                    return asdict(
                        await scope.rename_folder(
                            params["name"], params["new_name"], expected=expected
                        )
                    )
                if action == "x_delete_group_folder":
                    return asdict(
                        await scope.delete_folder(params["name"], expected=expected)
                    )
                if action == "x_move_group_file":
                    return asdict(
                        await scope.move_file(
                            params["name"],
                            params.get("folder"),
                            source_folder=params.get("source_folder"),
                            expected=expected,
                        )
                    )
                if action == "x_rename_group_file":
                    return asdict(
                        await scope.rename_file(
                            params["name"], params["new_name"], expected=expected
                        )
                    )
                if action == "x_download_group_file":
                    return asdict(
                        await scope.download_file(
                            params["name"], params["destination"], expected=expected
                        )
                    )
                return asdict(
                    await scope.delete_file(params["name"], expected=expected)
                )
            if action == "x_get_group_file_actions":
                return [
                    asdict(row)
                    for row in await scope.file_actions(
                        params["row_index"], query=params.get("query")
                    )
                ]
            if action == "x_get_group_file_sort_state":
                return asdict(await scope.file_sort_state())
            return [asdict(row) for row in await scope.file_sort_options()]
        if action in (
            "x_get_controls",
            "x_get_chat_settings",
            "x_set_pinned",
            "x_get_draft",
            "x_set_draft",
            "x_clear_draft",
            "x_send_draft",
            "x_get_member_profile",
            "x_get_member_actions",
            "x_get_message_actions",
        ):
            scope = self.qq.conversation(str(params["target_id"]))
            if action == "x_get_controls":
                return [
                    asdict(row)
                    for row in await scope.controls(area=params.get("area", "chat"))
                ]
            if action == "x_get_chat_settings":
                return asdict(await scope.settings())
            if action == "x_set_pinned":
                return asdict(await scope.set_pinned(params["enabled"]))
            if action == "x_get_draft":
                return asdict(await scope.draft())
            if action == "x_set_draft":
                return asdict(await scope.set_draft(params["text"]))
            if action == "x_clear_draft":
                return asdict(
                    await scope.clear_draft(expected_text=params["expected_text"])
                )
            if action == "x_send_draft":
                return self._sent_id(
                    scope, await scope.send_draft(expected_text=params["expected_text"])
                )
            if action == "x_get_member_profile":
                return asdict(await scope.member_profile(params["name"]))
            if action == "x_get_member_actions":
                return [
                    asdict(row) for row in await scope.member_actions(params["name"])
                ]
            kind, number, ui_id = self.index.resolve(self.self_id, params["message_id"])
            if (kind, number) != (scope.target.kind, scope.id):
                raise ValueError("Message belongs to another conversation")
            return [asdict(row) for row in await scope.message_actions(ui_id)]
        if action == "x_get_targets":
            return [asdict(t) for t in self.qq.targets]
        if action == "x_get_uia_status":
            return asdict(await self.qq.status())
        if action in (
            "x_get_ui_message",
            "x_get_history",
            "x_get_members",
            "x_send_file",
        ):
            scope = self.qq.conversation(str(params["target_id"]))
            if action == "x_get_ui_message":
                kind, target_id, ui_id = self.index.resolve(
                    self.self_id, params["message_id"]
                )
                if (kind, target_id) != (scope.target.kind, scope.id):
                    raise ValueError("Message scope mismatch")
                return asdict(await scope.get_message(ui_id))
            if action == "x_get_history":
                return [
                    asdict(r)
                    for r in await scope.history(
                        query=params.get("query"),
                        pages=params.get("pages", 1),
                        limit=params.get("limit", 100),
                        category=params.get("category", "all"),
                        **{k: params[k] for k in ("sender", "on_date") if k in params},
                    )
                ]
            if action == "x_get_members":
                if not isinstance(params.get("resolve_ids", False), bool):
                    raise TypeError("resolve_ids must be boolean")
                return [
                    asdict(m)
                    for m in await scope.members(
                        query=params.get("query"),
                        resolve_ids=params.get("resolve_ids", False),
                    )
                ]
            receipt = await scope.send_file(params["file"])
            return self._sent_id(scope, receipt)
        if action in ("x_get_capabilities", "x_get_capability_matrix"):
            return {
                "native_capabilities": [asdict(c) for c in CAPABILITIES],
                "actions": [
                    "send_group_msg",
                    "send_private_msg",
                    "send_msg",
                    "get_msg",
                    "get_image",
                    "x_get_image",
                    "x_download_message_file",
                    "get_login_info",
                    "get_version_info",
                    "x_get_client_version",
                    "x_get_storage_paths",
                    "get_group_info",
                    "can_send_image",
                    "can_send_record",
                    "x_get_targets",
                    "x_get_uia_status",
                    "x_get_ui_message",
                    "x_get_history",
                    "x_get_members",
                    "x_send_file",
                    "x_send_face",
                    "x_mark_unread",
                    "x_get_capabilities",
                    "get_group_member_info",
                    "get_group_member_list",
                    "set_group_card",
                    "x_set_member_card",
                    "group_poke",
                    "x_poke_member",
                    "x_poke_friend",
                    "set_group_whole_ban",
                    "delete_msg",
                    "set_essence_msg",
                    "delete_essence_msg",
                    "x_get_message_essence",
                    "x_get_reactions",
                    "x_add_reaction",
                    "x_remove_own_reaction",
                    "x_get_group_preferences",
                    "x_set_muted",
                    "x_get_auto_download",
                    "x_set_auto_download",
                    "x_set_group_speak_limit",
                    "x_set_group_message_mode",
                    "forward_group_single_msg",
                    "forward_friend_single_msg",
                    "x_get_controls",
                    "x_get_chat_settings",
                    "x_set_pinned",
                    "x_get_draft",
                    "x_set_draft",
                    "x_clear_draft",
                    "x_send_draft",
                    "x_get_member_profile",
                    "x_get_member_actions",
                    "x_get_message_actions",
                    "x_get_capability_matrix",
                    "x_get_menu",
                    "x_set_blocked",
                    "x_set_member_ban",
                    "set_group_ban",
                    "set_group_admin",
                    "x_set_member_admin",
                    "x_kick_member",
                    "set_group_kick",
                    "set_group_leave",
                    "x_get_notifications",
                    "set_friend_add_request",
                    "set_group_add_request",
                    *SECONDARY_ACTIONS,
                ],
                "partial_onebot11": True,
                "group_sender_id_requires_explicit_mapping_or_recent_uia_profiles": True,
            }
        raise Unsupported(f"Action {action!r} is not implemented by the UIA adapter")
