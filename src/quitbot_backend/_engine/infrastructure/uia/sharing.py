"""Observed contact-card and group-QR paths; no protocol payloads or IDs."""

import hashlib
import re
from contextlib import contextmanager
from pathlib import Path

from ...discovery import profile_qq_id
from ...errors import ControlUnavailable, DraftNotEmpty, TargetMismatch
from ...locking import ui_transaction
from ...models import ConversationInfo, editor_has_content, text_of
from ...uia import wait_until
from .chat import checked_forwarding_labels, forwarding_label
from .group_files import GroupFilesUI, file_name
from .moderation import mutation_guard
from .profile_cards import ProfileCards


def contact_card_name(node):
    cards = [n for n in node.walk() if n.has_class("com_tencent_contact_lua")]
    if len(cards) != 1:
        raise ControlUnavailable("Only the observed QQ contact-card type is supported")
    labels = [n.name for n in cards[0].walk() if n.type == "TextControl" and n.name]
    if len(labels) != 2 or labels[1] != "推荐好友":
        raise ControlUnavailable("Contact-card summary is ambiguous")
    return labels[0]


class SharingUI:
    def __init__(self, client):
        self.client, self.b, self.ui = client, client.backend, client.control_ui

    def _button(self, root, name):
        return self.b.find(
            root, lambda c: c.ControlTypeName == "ButtonControl" and c.Name == name
        )

    def _clean(self):
        if editor_has_content(self.b.snapshot(self.b.editor())):
            raise DraftNotEmpty("Existing draft retained")
        if self.ui.by_class("q-dialog-main") is not None:
            raise ControlUnavailable("Existing dialog retained")
        if self.ui.by_class("forward-ops") is not None:
            raise ControlUnavailable("Existing message multiselection retained")

    def _close_multi(self):
        close = self.ui.by_class("op-close-icon")
        if close is not None:
            self.b.invoke(close)
            wait_until(lambda: self.ui.by_class("forward-ops"), lambda c: c is None)

    @contextmanager
    def profile_card(self, target, trigger=None):
        probe = ProfileCards(self.b)
        self.b.invoke(
            trigger or self.ui.by_class("chat-header__contact-name", required=True)
        )

        def matches(fields):
            try:
                return profile_qq_id(fields) == target.id
            except ControlUnavailable:
                return False

        result = wait_until(lambda: probe.refreshed(matches))
        card = probe.reveal(result)
        try:
            yield card, result[2]
        finally:
            card.GetWindowPattern().Close(waitTime=0.1)

    def profile(self, key, *, full=False):
        b, ui = self.b, self.ui
        with ui_transaction():
            target = self.client.target(key)
            if target.kind != "private":
                raise ValueError("Profile requires a private conversation")
            self.client._open(target)
            b.profile(target.id, target.name, target.kind)
            self._clean()
            try:
                with self.profile_card(target) as (card, fields):
                    if not full:
                        return ConversationInfo(
                            target.id, target.name, "private", fields
                        )
                    b.invoke(self._button(card, f"查看{target.name}的个人主页"))
                    panel = wait_until(
                        lambda: ui.by_class("contact-profile", b.main_window)
                    )
                    identity = text_of(
                        b.snapshot(
                            ui.by_class("buddy-profile__info-uin", panel, required=True)
                        )
                    )
                    if target.id not in re.findall(r"\d+", identity):
                        raise TargetMismatch(
                            "Full profile identifies a different QQ account"
                        )
                    fields = tuple(
                        c.Name
                        for c in b.walk(panel)
                        if c.ControlTypeName == "TextControl" and c.Name
                    )
                    return ConversationInfo(target.id, target.name, "private", fields)
            finally:
                self.client._open(target)

    def like(self, key):
        submitted = [False]
        with (
            ui_transaction(),
            mutation_guard(submitted),
            self.ui.checked(key) as target,
        ):
            if target.kind != "private":
                raise ValueError("Like requires a private conversation")
            self._clean()

            def count(card):
                node = self.ui.by_class("total-likes", card)
                return text_of(self.b.snapshot(node)) if node is not None else None

            with self.profile_card(target) as (card, _):
                before = count(card)
                submitted[0] = True
                self.b.invoke(self._button(card, "点赞"))
                after = wait_until(
                    lambda: count(card), lambda v: v is not None and v != before
                )
            with self.profile_card(target) as (card, _):
                wait_until(lambda: count(card), lambda v: v == after)
            return {
                "user_id": target.id,
                "before_count_text": before,
                "after_count_text": after,
                "submitted": True,
            }

    def contact_profile(self, key, message_id, *, expected_user_id):
        with self.ui.checked(key):
            row = self._card(message_id)
            name = contact_card_name(self.b.snapshot(row))
            app = self.ui.by_class("rich-ui-app-container", row, required=True)
            choices = [
                c
                for c in app.GetChildren()
                if c.GetPattern(self.b.auto.PatternId.InvokePattern)
            ]
            if len(choices) != 1:
                raise ControlUnavailable("Contact card has no unique Invoke child")
            expected = str(expected_user_id)
            if not expected.isdigit():
                raise ValueError("expected_user_id must be numeric")
            from ...models import Target

            with self.profile_card(Target(expected, "", "private"), choices[0]) as (
                _,
                fields,
            ):
                return ConversationInfo(expected, name, "private", fields)

    def _card(self, message_id):
        row = self.b.find(
            self.b.window,
            lambda c: (
                c.AutomationId == str(message_id) and "ml-item" in c.ClassName.split()
            ),
        )
        contact_card_name(self.b.snapshot(row))
        return row

    def _select_recipient(self, dialog, target):
        b, ui = self.b, self.ui
        search = b.find(
            dialog, lambda c: c.Name == "搜索" and c.ControlTypeName == "EditControl"
        )
        search.GetValuePattern().SetValue(target.name, waitTime=0.3)
        rows = wait_until(
            lambda: [
                c
                for c in b.walk(dialog)
                if "list-item" in c.ClassName.split()
                and forwarding_label(b.snapshot(c), target.kind) == target.name
            ]
        )
        if len(rows) != 1 or checked_forwarding_labels(b.snapshot(dialog), target.kind):
            raise TargetMismatch("Recipient is ambiguous or already selected")
        b.invoke(rows[0])
        selected = ui.by_class("selected-list", dialog, required=True)
        wait_until(
            lambda: [
                c.Name
                for c in b.walk(selected)
                if c.ControlTypeName == "TextControl" and c.Name
            ],
            lambda v: v == [target.name],
        )
        note = b.find(
            dialog, lambda c: c.Name == "编辑器" and c.ControlTypeName == "EditControl"
        )
        value = note.GetValuePattern().Value.strip()
        if value and not (
            value == "留言" and ui.by_class("ck-placeholder", note) is not None
        ):
            raise DraftNotEmpty("Existing sharing note retained")

    @contextmanager
    def qr(self, key):
        with self.ui.checked(key) as target:
            if target.kind != "group":
                raise ValueError("QR sharing requires a group")
            self._clean()
            with self.ui.more() as panel:
                self.b.invoke(
                    self.ui.by_class("group-profile-card__share", panel, required=True)
                )
                dialog = wait_until(lambda: self.ui.by_class("qrcode-viewer-dialog"))
                try:
                    fields = [
                        c.Name
                        for c in self.b.walk(dialog)
                        if c.ControlTypeName == "TextControl"
                    ]
                    if target.id not in fields or "群号：" not in fields:
                        raise TargetMismatch("QR group number differs")
                    yield target, dialog
                finally:
                    if self.ui.by_class("qrcode-viewer-dialog") is not None:
                        self.b.invoke(self._button(dialog, "关闭"))
                        wait_until(
                            lambda: self.ui.by_class("qrcode-viewer-dialog"),
                            lambda c: c is None,
                        )

    def _qr_action(self, dialog, label):
        self.b.invoke(
            self.b.find(
                dialog,
                lambda c: (
                    "btn-item" in c.ClassName.split()
                    and text_of(self.b.snapshot(c)) == label
                ),
            )
        )

    def save_qr(self, key, destination):
        requested = Path(destination).expanduser()
        file_name(requested.name)
        path = requested.parent.resolve(strict=True) / requested.name
        if path.suffix.lower() != ".png":
            raise ValueError("Group QR destination must be PNG")
        if path.exists() or path.is_symlink():
            raise FileExistsError(path)
        submitted = [False]
        with (
            ui_transaction(),
            mutation_guard(submitted),
            self.qr(key) as (target, dialog),
        ):
            files = GroupFilesUI(self.client)
            self._qr_action(dialog, "保存图片")
            picker = wait_until(lambda: files._picker(self.b.window))
            try:
                field = self.b.find(
                    picker,
                    lambda c: (
                        c.AutomationId == "1001" and c.ControlTypeName == "EditControl"
                    ),
                )
                field.GetValuePattern().SetValue(str(path), waitTime=0.1)
                if (
                    field.GetValuePattern().Value != str(path)
                    or path.exists()
                    or path.is_symlink()
                ):
                    raise TargetMismatch("Save destination changed")
                submitted[0] = True
                self.b.invoke(self._button(picker, "另存为"))
                wait_until(lambda: files._picker(self.b.window), lambda c: c is None)
                wait_until(lambda: path.is_file() and path.stat().st_size > 24)
                data = path.read_bytes()
                if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ControlUnavailable("Saved QR is not PNG")
                return {
                    "group_id": target.id,
                    "path": str(path),
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            finally:
                if files._picker(self.b.window) is not None:
                    self.b.invoke(
                        self.b.find(
                            picker,
                            lambda c: (
                                c.AutomationId == "2"
                                and c.ControlTypeName == "ButtonControl"
                            ),
                        )
                    )

    def share(self, key, destination, *, kind, message_id=None):
        source, target = self.client.target(key), self.client.target(destination)
        if target.kind != "group":
            raise ValueError(
                "This sharing path was validated for group recipients only"
            )
        if kind not in {"contact", "qr", "card"}:
            raise ValueError("Unsupported sharing kind")
        b, ui = self.b, self.ui
        submitted = [False]
        with ui_transaction(), mutation_guard(submitted):
            with ui.checked(target):
                self._clean()
                previous = {m.id for m in self.client._read(target)}
            with ui.checked(source):
                self._clean()
                from contextlib import ExitStack

                with ExitStack() as stack:
                    if kind == "contact":
                        if source.kind != "private":
                            raise ValueError("Contact sharing requires a friend")
                        card, _ = stack.enter_context(self.profile_card(source))
                        b.invoke(self._button(card, "分享"))
                        expected_text = source.name + "推荐好友"
                    elif kind == "qr":
                        _, qr = stack.enter_context(self.qr(source))
                        self._qr_action(qr, "转发")
                        expected_text = None
                    else:
                        row = self._card(message_id)
                        expected_text = contact_card_name(b.snapshot(row)) + "推荐好友"
                        from comtypes.gen.UIAutomationClient import (
                            IUIAutomationElement3,
                        )

                        ui.by_class(
                            "ark-msg-content-container", row, required=True
                        ).Element.QueryInterface(
                            IUIAutomationElement3
                        ).ShowContextMenu()
                        stack.callback(ui._dismiss_menu)
                        stack.callback(self._close_multi)
                        b.invoke(ui._menu_item(wait_until(ui.navigation._menu), "多选"))
                        toolbar = wait_until(lambda: ui.by_class("forward-ops"))
                        selected = [
                            c.AutomationId
                            for c in b.walk(b.window)
                            if "ml-item" in c.ClassName.split()
                            and any(
                                "msg-picker" in x.ClassName.split()
                                and "is-checked" in x.ClassName.split()
                                for x in b.walk(c)
                            )
                        ]
                        if selected != [str(message_id)]:
                            raise TargetMismatch("Selected message set differs")
                        item = b.find(
                            toolbar,
                            lambda c: (
                                c.Name == "逐条转发"
                                and c.ControlTypeName == "ListItemControl"
                            ),
                        )
                        b.invoke(
                            b.find(
                                item,
                                lambda c: (
                                    c.ControlTypeName == "ButtonControl" and c.IsEnabled
                                ),
                            )
                        )
                    dialog = wait_until(lambda: ui.by_class("selector-dialog"))
                    try:
                        self._select_recipient(dialog, target)
                        if b.header().Name != source.name:
                            raise TargetMismatch("Source changed before sharing")
                        submitted[0] = True
                        b.invoke(self._button(dialog, "确定"))
                        wait_until(
                            lambda: ui.by_class("selector-dialog"), lambda c: c is None
                        )
                    finally:
                        if (
                            not submitted[0]
                            and ui.by_class("selector-dialog") is not None
                        ):
                            b.invoke(self._button(dialog, "取消"))
                        close = ui.by_class("op-close-icon")
                        if close is not None:
                            b.invoke(close)
                        ui._dismiss_menu()
            try:
                self.client._open(target)

                def received():
                    return [
                        m
                        for m in self.client._read(target)
                        if m.id not in previous
                        and m.direction == "outgoing"
                        and (
                            (
                                kind != "qr"
                                and m.kind == "card"
                                and m.text == expected_text
                            )
                            or (
                                kind == "qr"
                                and m.kind == "mixed"
                                and len(m.attachments) == 1
                                and m.attachments[0].kind == "image"
                                and re.fullmatch(
                                    re.escape(f"点击链接加入群聊【{source.name}】：")
                                    + r"https://qm\.qq\.com/q/[A-Za-z0-9_-]+",
                                    m.text.strip(),
                                )
                            )
                        )
                    ]

                rows = wait_until(received)
                if len(rows) != 1:
                    raise ControlUnavailable("Sharing receipt is ambiguous")
                return rows[0]
            finally:
                self.client._open(source)
