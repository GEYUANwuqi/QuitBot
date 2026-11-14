import time
from collections.abc import Iterable, Iterator

from .errors import (
    ControlUnavailable,
    DraftNotEmpty,
    SendOutcomeUnknown,
    TargetMismatch,
)
from .locking import ui_transaction
from .models import (
    TEST_TARGETS,
    ConversationInfo,
    Message,
    MessageCursor,
    SendReceipt,
    Target,
    editor_has_content,
    editor_text,
    message_ready,
    parse_messages,
)
from .ui.backend import DesktopBackend, wait_until


class QQClient:
    @property
    def control_ui(self):
        if not hasattr(self, "_control_ui"):
            from .ui.workflows.chat import ChatUI

            self._control_ui = ChatUI(self)
        return self._control_ui

    def __init__(
        self,
        account: str | None = None,
        *,
        hwnd: int | None = None,
        targets: Iterable[Target] = TEST_TARGETS,
    ):
        self.targets = tuple(targets)
        with ui_transaction():
            self.backend = DesktopBackend(account, hwnd)
        self._verified = set()

    def target(self, key: str | Target) -> Target:
        if isinstance(key, Target):
            key = key.id
        matches = [t for t in self.targets if key in (t.id, t.name)]
        if len(matches) != 1:
            raise TargetMismatch(f"Unknown or ambiguous configured target: {key}")
        return matches[0]

    def _open(self, target):
        self.backend.check_thread()
        self.backend.open_target(target)

    def open_chat(self, key: str | Target, *, route: str = "auto") -> ConversationInfo:
        """Open from recent sessions or the address book, then verify numeric ID."""
        target = self.target(key)
        with ui_transaction():
            self.backend.open_target(target, route)
            fields = self.backend.profile(target.id, target.name, target.kind)
            self._verified.add(target.id)
            return ConversationInfo(target.id, target.name, target.kind, fields)

    def search(self, query: str, *, limit: int = 30):
        if not query.strip() or len(query) > 100 or any(ord(c) < 32 for c in query):
            raise ValueError(
                "Search query must be 1..100 characters without control characters"
            )
        if not 1 <= limit <= 100:
            raise ValueError("Search limit must be 1..100")
        with ui_transaction():
            return self.backend.search(query, limit)

    def conversation_info(self, key: str | Target) -> ConversationInfo:
        target = self.target(key)
        with ui_transaction():
            self._open(target)
            fields = self.backend.profile(target.id, target.name, target.kind)
            return ConversationInfo(target.id, target.name, target.kind, fields)

    def open_detached(self, key):
        target = self.target(key)
        with ui_transaction():
            result = self.backend.open_detached(target)
            self._verified.add(target.id)
            return result

    def read_detached(self, key, *, refresh=False):
        if type(refresh) is not bool:
            raise ValueError("refresh must be a boolean")
        target = self.target(key)
        with ui_transaction():
            self.backend.check_thread()
            if target.id not in self.backend._detached:
                raise ControlUnavailable(
                    "Open and verify the detached chat before polling"
                )
            self.backend.activate_detached(target, required=True)
            if refresh:
                self.backend.refresh_detached(target)
            return self._read(target)

    def _read(self, target):
        if self.backend.header().Name != target.name:
            raise TargetMismatch("Active conversation changed while reading")
        root = self.backend.find(
            self.backend.window, lambda c: c.AutomationId == "ml-root"
        )
        messages = parse_messages(
            self.backend.snapshot(root),
            target.id,
            peer_name=target.name if target.kind == "private" else None,
        )
        if self.backend.header().Name != target.name:
            raise TargetMismatch("Active conversation changed during snapshot")
        return messages

    def read_messages(self, key: str | Target) -> list[Message]:
        """Read loaded UI messages; does not promise full server history."""
        target = self.target(key)
        with ui_transaction():
            self._open(target)
            if target.id not in self._verified:
                self.backend.profile(target.id, target.name, target.kind)
                self._verified.add(target.id)
            return self._read(target)

    def send_text(
        self, key: str | Target, text: str, *, timeout: float = 10
    ) -> SendReceipt:
        if (
            not text
            or not text.strip()
            or "\r" in text
            or any(ord(c) < 32 and c != "\n" for c in text)
        ):
            raise ValueError(
                "Text must be nonempty; only newline control characters are accepted"
            )
        if len(text) > 3000:
            raise ValueError("Split messages longer than 3000 characters explicitly")
        target = self.target(key)
        with ui_transaction():
            self._open(target)
            # Numeric identity is checked from QQ's actual profile card every send.
            self.backend.profile(target.id, target.name, target.kind)
            previous = {m.id for m in self._read(target)}
            editor = self.backend.editor()
            if editor_has_content(self.backend.snapshot(editor)):
                raise DraftNotEmpty("Existing QQ draft retained; send aborted")
            editor = self.backend.focus_editor()
            self.backend.type_literal(editor, text)
            wait_until(
                lambda: editor_text(self.backend.snapshot(editor)).strip("\n"),
                lambda value: value == text.strip("\n"),
            )
            if self.backend.header().Name != target.name:
                raise TargetMismatch("Conversation changed before send")
            self.backend.ensure_focus(editor)
            button = self.backend.by_class("send-msg")
            wait_until(lambda: button.IsEnabled)
            try:
                self.backend.invoke(button)
                matches = wait_until(
                    lambda: [
                        m
                        for m in self._read(target)
                        if m.id not in previous
                        and m.direction == "outgoing"
                        and m.text == text
                    ],
                    timeout=timeout,
                )
                wait_until(
                    lambda: editor_text(
                        self.backend.snapshot(self.backend.editor())
                    ).strip(),
                    lambda draft: not draft,
                    timeout=timeout,
                )
            except Exception as exc:
                raise SendOutcomeUnknown(
                    "Send result uncertain; inspect QQ before retrying"
                ) from exc
            return SendReceipt(target.id, matches[-1].id, text)

    def get_message(self, key, message_id):
        from .operations import MessageOperations

        return MessageOperations(self).get(key, message_id)

    def reply_text(self, key, message_id, text, *, timeout=10):
        from .operations import MessageOperations

        return MessageOperations(self).reply(key, message_id, text, timeout=timeout)

    def send_file(self, key, path, *, timeout=30):
        from .operations import MessageOperations

        return MessageOperations(self).media(key, path, "file", timeout=timeout)

    def send_image(self, key, path, *, timeout=30):
        from .operations import MessageOperations

        return MessageOperations(self).media(key, path, "image", timeout=timeout)

    def member_details(self, key, *, query=None, resolve_ids=False):
        return self.control_ui.members(key, query=query, resolve_ids=resolve_ids)

    def instance_status(self):
        from .models import InstanceStatus

        with ui_transaction():
            self.backend.check_thread()
            b = self.backend
            avatar = b.find(
                getattr(b, "main_window", b.window),
                lambda x: (
                    x.ControlTypeName == "ButtonControl"
                    and x.Name == f"{b.account}的头像"
                ),
                required=False,
            )
            return InstanceStatus(
                b.account,
                b.hwnd,
                b.window.ProcessId,
                avatar is not None,
                self_id=getattr(b, "self_id", None),
            )

    def watch(
        self,
        keys: Iterable[str | Target],
        *,
        interval: float = 1.0,
        include_existing: bool = False,
        incoming_only: bool = True,
        duration: float | None = None,
        on_ready=None,
        stop_event=None,
        mode="switch",
        refresh_detached=False,
    ) -> Iterator[Message]:
        """Poll configured loaded conversations, deduplicating stable message IDs.

        First iteration primes each conversation. There is no global/hidden-message
        hook; high volume messages can leave QQ's virtualized UI before polling.
        """
        if mode not in ("switch", "detached"):
            raise ValueError("mode must be switch or detached")
        if type(refresh_detached) is not bool or (
            refresh_detached and mode != "detached"
        ):
            raise ValueError("refresh_detached requires detached mode and a boolean")
        targets = [self.target(k) for k in keys]
        if not targets or interval < 0.2:
            raise ValueError("Provide targets and interval >= 0.2 seconds")
        cursor = MessageCursor()

        def read(target):
            if mode == "detached":
                return (
                    self.read_detached(target, refresh=True)
                    if refresh_detached
                    else self.read_detached(target)
                )
            return self.read_messages(target)

        start = time.monotonic()
        for target in targets:
            if mode == "detached":
                self.open_detached(target)
            existing = read(target)
            cursor.prime(existing)
            if include_existing:
                yield from (
                    m
                    for m in existing
                    if not incoming_only or m.direction == "incoming"
                )
        if on_ready is not None:
            on_ready()
        while (duration is None or time.monotonic() - start < duration) and not (
            stop_event is not None and stop_event.is_set()
        ):
            for target in targets:
                loaded = read(target)
                # QQ may expose an item ID before its sender/body is populated.
                # Do not consume that ID before it becomes an incoming message.
                eligible = [
                    m
                    for m in loaded
                    if not incoming_only
                    or (m.direction == "incoming" and message_ready(m))
                ]
                yield from cursor.take(eligible)
            time.sleep(interval)

    def recent_contacts(self) -> list[str]:
        """Loaded recent session titles, not the complete QQ address book."""
        from .ui.workflows.recent import RecentConversations

        with ui_transaction():
            self.backend.check_thread()
            self.backend.navigate("消息")
            return [name for name, _ in RecentConversations(self.backend).rows()]

    def group_members(self, key: str | Target) -> list[str]:
        target = self.target(key)
        if target.kind != "group":
            raise ValueError("Group members require a group target")
        with ui_transaction():
            self._open(target)
            pane = self.backend.find(
                self.backend.window,
                lambda c: c.Name == "群成员列表" and c.ControlTypeName == "PaneControl",
            )
            names = []
            for row in self.backend.walk(pane):
                if "group-user" in row.ClassName.split():
                    texts = [
                        c.Name
                        for c in self.backend.walk(row)
                        if c.ControlTypeName == "TextControl" and c.Name
                    ]
                    if texts:
                        names.append(texts[0])
            return names

    def diagnostics(self) -> dict:
        """Report exposed native controls without dumping chat content."""
        with ui_transaction():
            self.backend.check_thread()
            self.backend.navigate("消息")
            window = self.backend.window
            # All active chats may have been detached from the main window.
            find_class = lambda name: self.backend.find(
                window, lambda c: name in c.ClassName.split(), required=False
            )
            editor = find_class("ExEditor-qq-msg-editor")
            send = find_class("send-msg")
            header = find_class("chat-header__contact-name")
            return {
                "account": self.backend.account,
                "hwnd": window.NativeWindowHandle,
                "pid": window.ProcessId,
                "native_window_class": window.ClassName,
                "active_conversation": header.Name if header is not None else None,
                "editor_class": editor.ClassName if editor is not None else None,
                "editor_value_pattern": bool(
                    editor is not None
                    and editor.GetPattern(self.backend.auto.PatternId.ValuePattern)
                ),
                "send_invoke_pattern": bool(
                    send is not None and send.GetInvokePattern()
                ),
                "message_root_id": "ml-root",
                "input_backend": getattr(self.backend.auto, "input_backend", "UIA SetFocus + Unicode SendInput"),
                "mouse_operations": False,
            }

    def contacts(self, kind: str = "friends"):
        """Read loaded address-book rows via Toggle/Selection/Expand patterns."""
        from .models import ContactEntry

        if kind not in ("friends", "groups"):
            raise ValueError("kind must be friends or groups")
        with ui_transaction():
            b = self.backend
            b.check_thread()
            expanded = []
            try:
                b.navigate("联系人")
                tab_name = "好友" if kind == "friends" else "群聊"
                tab = b.find(
                    b.window,
                    lambda c: (
                        c.Name == tab_name and c.ControlTypeName == "TabItemControl"
                    ),
                )
                b.invoke(tab)
                pane = wait_until(
                    lambda: b.find(
                        b.window,
                        lambda c: (
                            c.Name == "联系人列表"
                            and c.ControlTypeName == "PaneControl"
                        ),
                        required=False,
                    )
                )
                headers = [
                    c
                    for c in b.walk(pane)
                    if "q-collapse-item__header" in c.ClassName.split()
                ]
                for header in headers:
                    pattern = header.GetPattern(b.auto.PatternId.ExpandCollapsePattern)
                    if pattern and pattern.ExpandCollapseState == 0:
                        pattern.Expand(waitTime=0.05)
                        expanded.append(pattern)
                time.sleep(0.2)
                group = ""
                result = []
                for item in b.walk(pane):
                    classes = item.ClassName.split()
                    if "q-collapse-item__header" in classes:
                        group = item.Name
                    elif "contact-item" in classes:
                        name = b.contact_name(item)
                        texts = [
                            c.Name
                            for c in b.walk(item)
                            if c.ControlTypeName == "TextControl" and c.Name
                        ]
                        if name:
                            result.append(
                                ContactEntry(
                                    name, group, "".join(texts).removeprefix(name), kind
                                )
                            )
                return result
            finally:
                for pattern in reversed(expanded):
                    pattern.Collapse(waitTime=0.05)
                b.navigate("消息")

    def history(
        self,
        key: str | Target,
        *,
        query: str | None = None,
        limit: int = 100,
        pages: int = 1,
        category: str = "all",
        sender: str | None = None,
        on_date: str | None = None,
    ):
        """Read loaded all/file history rows, preserving native tab/search state.

        History UI lacks stable message IDs. A files-only row may not expose
        message direction; its direction is then ``unknown``.
        """
        from .ui.workflows.history import HistoryUI

        filters = {
            k: v for k, v in (("sender", sender), ("on_date", on_date)) if v is not None
        }
        return HistoryUI(self).read(
            key, query=query, limit=limit, pages=pages, category=category, **filters
        )
