"""UI notification cards and conservative conversion to OneBot observations."""

import hashlib
import json
import re
import time
from dataclasses import dataclass

from quitbot_backend._engine.models import text_of


@dataclass(frozen=True)
class SystemCard:
    ui_key: str
    kind: str
    actor_name: str
    group_name: str | None
    summary: str
    comment: str
    time_text: str
    status: str
    actions: tuple[str, ...]


def parse_system_cards(root, kind):
    if kind not in ("group", "friend"):
        raise ValueError("Notification kind must be group or friend")
    result = []
    for row in root.walk():
        if not row.has_class("list-item"):
            continue
        nodes = list(row.walk())

        def field(class_name, nodes=nodes):
            return next((text_of(n) for n in nodes if n.has_class(class_name)), "")

        actor = field("card-title") or field("card-desc__name")
        if not actor:
            continue
        group = field("card-summary__name") or None
        summary = "".join(
            text_of(n)
            for n in nodes
            if n.has_class("card-summary-words")
            and not n.has_class("card-summary__name")
        )
        if kind == "friend":
            summary = "".join(
                text_of(n)
                for n in nodes
                if n.has_class("card-desc") and not n.has_class("card-desc__name")
            )
        comment = field("card-summary__postscript") or next(
            (
                n.name
                for n in nodes
                if n.type == "TextControl" and n.name.startswith(("留言：", "留言:"))
            ),
            "",
        )
        comment = re.sub(r"^留言[:：]\s*", "", comment)
        clock = field("secondary-info")
        wrapper = next(
            (n for n in nodes if n.has_class("sys-notify-card__action-wrapper")), None
        )
        actions = (
            tuple(n.name for n in wrapper.walk() if n.type == "ButtonControl")
            if wrapper
            else ()
        )
        selected = next((n for n in nodes if n.has_class("q-selected-area")), None)
        if selected is not None:
            selected_text = text_of(selected)
            actions = (selected_text,) if selected_text == "同意" else ()
            if actions and any(
                n.has_class("q-select-button-action__wrapper") for n in nodes
            ):
                actions += ("拒绝",)
        state = (
            "pending"
            if "同意" in actions or "拒绝" in actions
            else text_of(selected)
            if selected is not None
            else text_of(wrapper)
            if wrapper
            else "informational"
        )
        if state == "informational":
            state = next(
                (
                    n.name
                    for n in nodes
                    if n.type == "TextControl"
                    and n.name
                    in ("已同意", "已拒绝", "已添加", "已通过", "已忽略", "已过期")
                ),
                state,
            )
        payload = [kind, actor, group, summary, comment, clock]
        key = (
            "uia:"
            + hashlib.sha256(
                json.dumps(payload, ensure_ascii=False).encode()
            ).hexdigest()[:32]
        )
        result.append(
            SystemCard(key, kind, actor, group, summary, comment, clock, state, actions)
        )
    return result


class ActivityCursor:
    """Suppress initial history; duplicate identities never become fresh events."""

    def __init__(self):
        self.seen = set()
        self.primed = False

    def take(self, values, key):
        values = list(values)
        fresh = []
        for value in values:
            identity = key(value)
            if self.primed and identity not in self.seen:
                fresh.append(value)
            self.seen.add(identity)
        self.primed = True
        return fresh


def system_text(message):
    text = message.text
    if message.timestamp and text.startswith(message.timestamp):
        text = text[len(message.timestamp) :]
    else:
        text = re.sub(r"^\d{1,2}:\d{2}\s*", "", text.strip())
    return text.strip().rstrip("。")


def activity_message_key(message):
    # A recall replaces the existing message row without changing its UI ID.
    return (
        (message.id, "system", system_text(message))
        if message.direction == "system"
        else (message.id, "message", "")
    )


def recall_actor(message, target, self_id, member_ids):
    """Only observed own/peer recall wording; no administrator inference."""
    if message.direction != "system" or message.kind != "system":
        return None
    text = system_text(message).removesuffix("重新编辑").strip()
    match = re.fullmatch(r"(.+)撤回了一条消息", text)
    if not match:
        return None
    name = match[1]
    if target.kind == "private":
        # OB11 friend_recall.user_id identifies the friend, not the local sender.
        return int(target.id) if name in ("对方", target.name) else None
    if name == "你":
        return int(self_id)
    value = member_ids.get(name)
    return int(value) if value is not None else None


def private_poke_notice(message, target, self_id):
    """Map the two observed private poke forms; never parse ordinary chat text."""
    if (target.kind != "private" or message.conversation_id != target.id
            or message.direction != "system" or message.kind != "system"
            or not target.name or target.name == "你"):
        return None
    peer, own = int(target.id), int(self_id)
    if peer == own:
        return None
    text = system_text(message)
    if text == f"{target.name}戳了戳你":
        sender, recipient = peer, own
    elif text == f"你戳了戳{target.name}":
        sender, recipient = own, peer
    else:
        return None
    return {
        "time": int(time.time()), "self_id": own,
        "post_type": "notice", "notice_type": "notify", "sub_type": "poke",
        "user_id": peer, "sender_id": sender, "target_id": recipient,
        "quitbot": {
            "ui_message_id": message.id, "raw_text": message.text,
            "time_source": "observed", "identity_source": "verified_private_target",
        },
    }


def group_notice(message, self_id, member_ids):
    """Map observed system rows, keeping incomplete events explicitly nonstandard."""
    if message.direction != "system" or message.kind != "system":
        return None
    text = system_text(message)
    names = {name: int(number) for name, number in member_ids.items()}
    names["你"] = int(self_id)
    alternatives = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    if not alternatives:
        return None
    who = f"(?P<user>{alternatives})"
    operator = f"(?P<operator>{alternatives})"
    reaction = re.fullmatch(f"{operator}\\s+回应了你的\\s+消息:\\s*(?P<emoji>[^\\r\\n]*)", text)
    if reaction:
        return {
            "time": int(time.time()),
            "self_id": int(self_id),
            "post_type": "quitbot",
            "detail_type": "group_reaction_observation",
            "group_id": int(message.conversation_id),
            "user_id": names[reaction["operator"]],
            "target_id": int(self_id),
            "source_message_id": None,
            "emoji_id": None,
            "emoji_text": reaction["emoji"].strip() or None,
            "ui_message_id": message.id,
            "raw_text": message.text,
            "time_source": "observed",
            "identity_source": "explicit_mapping",
            "reason": "source_message_and_emoji_ids_unavailable",
        }
    poke = re.fullmatch(f"{operator}戳了戳{who}", text)
    if poke:
        return {
            "time": int(time.time()),
            "self_id": int(self_id),
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "group_id": int(message.conversation_id),
            "user_id": names[poke["operator"]],
            "target_id": names[poke["user"]],
            "quitbot": {
                "ui_message_id": message.id,
                "raw_text": message.text,
                "time_source": "observed",
                "identity_source": "explicit_mapping",
            },
        }
    patterns = [
        (f"{operator}邀请{who}加入了群聊", "group_increase", "invite"),
        (f"{who}被{operator}解除禁言", "group_ban", "lift_ban"),
        (
            f"{who}被{operator}禁言(?P<duration>\\d+)(?P<unit>分钟|小时|天)",
            "group_ban",
            "ban",
        ),
    ]
    for pattern, kind, subtype in patterns:
        match = re.fullmatch(pattern, text)
        if match:
            event = {
                "time": int(time.time()),
                "self_id": int(self_id),
                "post_type": "notice",
                "notice_type": kind,
                "sub_type": subtype,
                "group_id": int(message.conversation_id),
                "user_id": names[match["user"]],
                "operator_id": names[match["operator"]],
                "quitbot": {
                    "ui_message_id": message.id,
                    "raw_text": message.text,
                    "time_source": "observed",
                    "identity_source": "explicit_mapping",
                },
            }
            if kind == "group_ban":
                event["duration"] = (
                    0
                    if subtype == "lift_ban"
                    else int(match["duration"])
                    * {"分钟": 60, "小时": 3600, "天": 86400}[match["unit"]]
                )
            return event
    return None
