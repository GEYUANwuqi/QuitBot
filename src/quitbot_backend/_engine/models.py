import re
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class Target:
    id: str
    name: str
    kind: Literal["group", "private"] = "group"


@dataclass(frozen=True)
class ConversationCandidate:
    name: str
    selectable: bool = True
    reason: str | None = None


TEST_TARGETS: tuple[Target, ...] = ()


@dataclass(frozen=True)
class Node:
    type: str = ""
    name: str = ""
    class_name: str = ""
    automation_id: str = ""
    children: tuple["Node", ...] = ()

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def has_class(self, name: str) -> bool:
        return name in self.class_name.split()


@dataclass(frozen=True)
class Attachment:
    kind: Literal["image", "file"]
    name: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class QuotedMessage:
    sender: str | None
    text: str
    id: str | None = None


@dataclass(frozen=True)
class MessagePart:
    kind: Literal["text", "mention"]
    text: str


@dataclass(frozen=True)
class Message:
    id: str
    conversation_id: str
    sender: str | None
    text: str
    direction: Literal["incoming", "outgoing", "system"]
    timestamp: str | None = None
    kind: str = "text"
    attachments: tuple[Attachment, ...] = ()
    quote: QuotedMessage | None = None
    parts: tuple[MessagePart, ...] = ()
    face_count: int = 0


@dataclass(frozen=True)
class ConversationInfo:
    id: str
    name: str
    kind: str
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class SendReceipt:
    target_id: str
    message_id: str
    text: str
    verified_in_ui: bool = True


@dataclass(frozen=True, kw_only=True)
class MentionReceipt(SendReceipt):
    member_id: str
    member_name: str


@dataclass(frozen=True)
class MediaReceipt:
    target_id: str
    message_id: str
    kind: Literal["image", "file"]
    filename: str
    local_path: str
    verified_in_ui: bool = True


@dataclass(frozen=True)
class GroupMemberInfo:
    name: str
    role: Literal["owner", "admin", "member"]
    user_id: str | None = None
    nickname: str | None = None


@dataclass(frozen=True)
class InstanceStatus:
    account: str
    hwnd: int
    pid: int
    ui_available: bool
    online: bool | None = None
    self_id: str | None = None


@dataclass(frozen=True)
class HistoryRecord:
    conversation_id: str
    sender: str | None
    text: str
    timestamp: str | None
    direction: str
    # QQ history UI does not expose the stable message IDs of the live list.
    id: str | None = None
    kind: str = "text"
    attachments: tuple[Attachment, ...] = ()
    quote: QuotedMessage | None = None


@dataclass(frozen=True)
class ContactEntry:
    name: str
    group: str
    summary: str
    kind: str
    id: str | None = None


@dataclass(frozen=True)
class SearchResult:
    category: str
    label: str
    name: str | None = None
    id: str | None = None


def text_of(node: Node) -> str:
    return "".join(n.name for n in node.walk() if n.type == "TextControl")


def editor_text(node: Node) -> str:
    """ProseMirror exposes paragraphs as anonymous sibling UIA groups."""
    if node.has_class("is-empty") and all(
        c.type == "TextControl" for c in node.children
    ):
        visible = [
            c.name
            for c in node.children
            if not c.has_class("ProseMirror-trailingBreak")
        ]
        if not visible or visible == ["按住 Win + Alt，使用语音输入文字"]:
            return ""
    children = tuple(
        c for c in node.children if not c.has_class("editor-el__reply-wrapper")
    )
    if children and all(
        child.type == "GroupControl" and not child.class_name for child in children
    ):
        return "\n".join(text_of(child).rstrip("\n") for child in children)
    return "".join(text_of(c) for c in children)


def editor_has_content(node: Node) -> bool:
    return bool(editor_text(node).strip()) or any(
        n.has_class("editor-el__reply-wrapper")
        or n.has_class("editor-el--inline-block")
        for n in node.walk()
    )


def body_text(node: Node) -> str:
    if node.has_class("reply-element"):
        return ""
    if node.type == "TextControl":
        return node.name
    return "".join(body_text(c) for c in node.children)


def body_nodes(node: Node):
    if node.has_class("reply-element"):
        return
    yield node
    for child in node.children:
        yield from body_nodes(child)


def message_parts(node: Node):
    if node.has_class("reply-element"):
        return ()
    if node.has_class("text-element--at"):
        return (MessagePart("mention", text_of(node).removeprefix("@")),)
    if node.type == "TextControl":
        return (MessagePart("text", node.name),)
    result = []
    for child in node.children:
        for part in message_parts(child):
            if result and result[-1].kind == part.kind == "text":
                result[-1] = MessagePart("text", result[-1].text + part.text)
            else:
                result.append(part)
    return tuple(result)


def parse_messages(
    root: Node, target_id: str, *, peer_name: str | None = None
) -> list[Message]:
    result = []
    for item in root.walk():
        if not item.has_class("ml-item") or not item.automation_id:
            continue
        nodes = list(item.walk())
        body = next((n for n in nodes if n.has_class("message-content")), None)
        forward = next((n for n in nodes if n.has_class("forward-msg")), None)
        card = next(
            (n for n in nodes if n.has_class("ark-msg-content-container")), None
        )
        if card is None and body:
            card = next((n for n in body_nodes(body) if n.has_class("flash-transfer")), None)
        sender = next(
            (n.name for n in nodes if n.has_class("avatar-span") and n.name), None
        )
        timestamp = next(
            (text_of(n) for n in nodes if n.has_class("message__timestamp")), None
        )
        own = any("message-container--self" in n.class_name.split() for n in nodes)
        incoming = any(
            n.has_class("container--others") or n.has_class("message-container--others")
            for n in nodes
        )
        if sender is None and incoming and not own and peer_name is not None:
            sender = peer_name
        image = body and any(
            n.type == "ImageControl"
            and (n.name == "图片" or n.has_class("pic-element"))
            for n in body_nodes(body)
        )
        face_count = (
            sum(n.has_class("face-element") for n in body_nodes(body)) if body else 0
        )
        random_sticker = body and any(
            n.has_class("is-random-sticker") for n in body_nodes(body)
        )
        file = next((n for n in nodes if n.has_class("file-element")), None)
        filename = next((text_of(n) for n in nodes if n.has_class("file-name")), None)
        if filename is None and file and file.has_class("msg-preview--video"):
            info = next((n for n in file.walk() if n.has_class("file-info-mask")), None)
            if info:
                filename = next(
                    (text_of(n) for n in info.walk() if n.has_class("text-ellipsis")),
                    None,
                )
        quote_node = next((n for n in nodes if n.has_class("reply-element")), None)
        quoted_parts = (
            [n.name for n in quote_node.walk() if n.type == "TextControl"]
            if quote_node
            else []
        )
        quote = (
            QuotedMessage(
                quoted_parts[0] if quoted_parts else None, "".join(quoted_parts[1:])
            )
            if quote_node
            else None
        )
        text = (
            text_of(forward)
            if forward
            else text_of(card)
            if card
            else body_text(body)
            if body
            else filename or text_of(item)
        )
        attachments = (
            (Attachment("file", filename, text_of(file)),)
            if file
            else (Attachment("image"),)
            if image
            else ()
        )
        result.append(
            Message(
                id=item.automation_id,
                conversation_id=target_id,
                sender=sender,
                text=text,
                direction="outgoing"
                if own
                else "incoming"
                if sender or incoming
                else "system",
                timestamp=timestamp,
                kind="forward"
                if forward
                else "card"
                if card
                else "random_sticker"
                if random_sticker
                else "file"
                if file
                else "mixed"
                if (image or face_count) and text
                else "image"
                if image
                else "face"
                if face_count
                else "text"
                if body
                else "system",
                attachments=attachments,
                quote=quote,
                parts=message_parts(body) if body and card is None else (),
                face_count=face_count,
            )
        )
    return result


def message_ready(message: Message) -> bool:
    if message.kind == "random_sticker":
        return True
    if message.kind in ("text", "forward", "card"):
        return bool(message.text)
    if message.kind == "file":
        return any(attachment.name for attachment in message.attachments)
    return message.kind in ("image", "mixed", "face") and bool(
        message.attachments or message.face_count
    )


def parse_search_results(root: Node) -> list[SearchResult]:
    """Keep contact hits distinct from chat history/member-match hits."""
    results = []
    category = "其他"

    def visit(node):
        nonlocal category
        if node.type == "ListItemControl":
            if not node.has_class("search-result-item"):
                return
            name = None
            number = None
            label = node.name
            if category == "联系人":
                title = label.split(" 来自:", 1)[0]
                match = re.fullmatch(r"(.*?) \((\d{5,12})\)", title)
                name, number = (match[1], match[2]) if match else (title, None)
            elif category == "群聊":
                match = re.match(r"^(.*) \(\d+人\)(?:\s|$)", label)
                name = match[1] if match else label
            results.append(SearchResult(category, label, name, number))
            return
        if node.type == "TextControl" and node.name in {
            "联系人",
            "群聊",
            "聊天记录",
            "文件",
            "小程序",
            "使用过的小程序",
        }:
            category = node.name
        for child in node.children:
            visit(child)

    visit(root)
    return results


def parse_history(root: Node, target_id: str) -> list[HistoryRecord]:
    records = []
    date = ""
    for item in root.walk():
        if item.has_class("record-msg-date"):
            date = text_of(item)
        elif item.has_class("record-msg-detail"):
            nodes = list(item.walk())
            body = next((n for n in nodes if n.has_class("message-content")), None)
            file = next(
                (
                    n
                    for n in nodes
                    if n.has_class("file-element") or n.has_class("record-file")
                ),
                None,
            )
            filename = next(
                (
                    text_of(n)
                    for n in nodes
                    if n.has_class("file-name") or n.has_class("record-file-name")
                ),
                None,
            )
            image = body is not None and any(
                n.type == "ImageControl"
                and (n.name == "图片" or n.has_class("pic-element"))
                for n in body_nodes(body)
            )
            reply = next((n for n in nodes if n.has_class("reply-element")), None)
            parts = (
                [n.name for n in reply.walk() if n.type == "TextControl"]
                if reply
                else []
            )
            quote = (
                QuotedMessage(parts[0] if parts else None, "".join(parts[1:]))
                if reply
                else None
            )
            content = body_text(body) if body else filename or ""
            sender = next(
                (n.name for n in nodes if n.has_class("avatar-span") and n.name), None
            )
            category_file = file is not None and file.has_class("record-file")
            if category_file:
                # The file tab exposes direct text metadata, not an avatar or
                # self-message marker. Only the observed separator shape maps
                # its final text to a sender; unknown shapes keep sender unset.
                metadata = [n.name for n in file.children if n.type == "TextControl"]
                if (
                    len(metadata) == 7
                    and metadata[1:6:2] == ["·", "·", "·"]
                    and re.fullmatch(r"\d{1,2}:\d{2}", metadata[0])
                ):
                    sender = metadata[6] or None
            clock = next(
                (
                    n.name
                    for n in nodes
                    if n.type == "TextControl"
                    and re.fullmatch(r"\d{1,2}:\d{2}", n.name)
                ),
                "",
            )
            own = any(n.has_class("message-container--self") for n in nodes)
            records.append(
                HistoryRecord(
                    target_id,
                    sender,
                    content,
                    f"{date} {clock}".strip() or None,
                    "unknown"
                    if category_file
                    else "outgoing"
                    if own
                    else "incoming"
                    if sender
                    else "system",
                    kind="file"
                    if file
                    else "mixed"
                    if image and content
                    else "image"
                    if image
                    else "text"
                    if body
                    else "unknown",
                    attachments=(Attachment("file", filename, text_of(file)),)
                    if file
                    else (Attachment("image"),)
                    if image
                    else (),
                    quote=quote,
                )
            )
    return records


@dataclass
class MessageCursor:
    """Deduplicate stable UI message IDs, preserving identical text messages."""

    seen: set[tuple[str, str]] = field(default_factory=set)

    def prime(self, messages):
        self.seen.update((m.conversation_id, m.id) for m in messages)

    def take(self, messages):
        fresh = [m for m in messages if (m.conversation_id, m.id) not in self.seen]
        self.prime(fresh)
        return fresh
