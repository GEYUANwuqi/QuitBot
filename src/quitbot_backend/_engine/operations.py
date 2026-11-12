"""Native message operations; all mutations remain scoped to one bound client."""

from pathlib import Path

from .errors import (
    ControlUnavailable,
    DraftNotEmpty,
    MessageNotFound,
    SendOutcomeUnknown,
    TargetMismatch,
)
from .locking import ui_transaction
from .models import MediaReceipt, SendReceipt, editor_has_content, editor_text, text_of
from .uia import wait_until


def validate_text(text):
    if (
        not isinstance(text, str)
        or not text.strip()
        or len(text) > 3000
        or any(ord(c) < 32 and c != "\n" for c in text)
    ):
        raise ValueError(
            "Text must be 1..3000 characters; only newline controls are accepted"
        )


def local_media_path(path, kind):
    value = Path(path).expanduser().resolve(strict=True)
    if not value.is_file():
        raise ValueError("Media path must reference a regular local file")
    if kind == "image" and value.suffix.lower() not in {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".webp",
    }:
        raise ValueError("Image must be png, jpg, jpeg, gif, bmp, or webp")
    return value


class MessageOperations:
    def __init__(self, client):
        self.client = client

    def _prepare(self, target):
        c = self.client
        c._open(target)
        c.backend.profile(target.id, target.name, target.kind)
        editor = c.backend.editor()
        if editor_has_content(c.backend.snapshot(editor)):
            raise DraftNotEmpty(
                "Existing QQ draft/quote/image retained; operation aborted"
            )
        return {m.id for m in c._read(target)}

    def _assert_target(self, target):
        if self.client.backend.header().Name != target.name:
            raise TargetMismatch("Conversation changed before sending")

    def get(self, key, message_id):
        target = self.client.target(key)
        with ui_transaction():
            self.client._open(target)
            matches = [
                m for m in self.client.read_messages(target) if m.id == str(message_id)
            ]
            if len(matches) != 1:
                raise MessageNotFound(
                    f"Message {message_id!r} is not loaded in {target.id}"
                )
            return matches[0]

    def reply(self, key, message_id, text, *, timeout=10):
        validate_text(text)
        c = self.client
        target = c.target(key)
        b = c.backend
        with ui_transaction():
            previous = self._prepare(target)
            original = self.get(target, message_id)
            if original.kind not in ("text", "mixed", "card", "file"):
                raise ControlUnavailable(
                    "Quoted reply supports loaded text, mixed, contact-card or file messages"
                )
            if original.kind == "file" and (
                len(original.attachments) != 1
                or original.attachments[0].name != original.text
                or not original.text
            ):
                raise ControlUnavailable("Quoted file requires one readable filename")
            root = b.find(b.window, lambda x: x.AutomationId == "ml-root")
            item = b.find(root, lambda x: x.AutomationId == original.id)
            if original.kind == "card":
                from quitbot_backend._engine.native import show_context_menu

                from .infrastructure.uia.sharing import contact_card_name

                card_name = contact_card_name(b.snapshot(item))
                content = b.by_class("ark-msg-content-container", root=item)
                show_context_menu(content)
            else:
                content = b.by_class(
                    "file-message--content"
                    if original.kind == "file"
                    else "msg-content-container",
                    root=item,
                )
                b.focus_control(content)
                b.auto.SendKeys("{Shift}{F10}", waitTime=0.1)
            menu = wait_until(
                lambda: b.find(
                    b.window,
                    lambda x: (
                        x.ControlTypeName == "MenuControl"
                        and "aio-right-click-menu" in x.ClassName.split()
                    ),
                    required=False,
                )
            )
            b.invoke(
                b.find(
                    menu,
                    lambda x: (
                        x.ControlTypeName == "MenuItemControl" and x.Name == "引用"
                    ),
                )
            )
            wait_until(
                lambda: b.find(
                    b.editor(),
                    lambda x: "editor-el__reply-wrapper" in x.ClassName.split(),
                    required=False,
                )
            )
            if original.text:
                preview = text_of(
                    b.snapshot(b.by_class("editor-el__reply-wrapper", root=b.editor()))
                )
                expected_preview = (
                    f"{original.sender}:推荐联系人：{card_name}"
                    if original.kind == "card"
                    else original.text
                )
                if (
                    preview != expected_preview
                    if original.kind == "card"
                    else expected_preview not in preview
                ):
                    raise TargetMismatch(
                        "Quote preview does not match the selected message"
                    )
            editor = b.focus_editor()
            initial = b.snapshot(editor)
            prefix = editor_text(initial).strip("\n")
            # QQ may add a genuine @mention when quoting another group member.
            # Accept only the native mention nodes and whitespace it created.
            remaining = prefix
            for node in initial.walk():
                if node.has_class("editor-el__at"):
                    remaining = remaining.replace(text_of(node), "", 1)
            if remaining.strip():
                raise TargetMismatch("Unexpected text appeared in the quote editor")
            expected_text = prefix + text
            b.type_literal(editor, text)
            wait_until(
                lambda: editor_text(b.snapshot(editor)).strip("\n"),
                lambda value: value == expected_text.strip("\n"),
            )
            self._assert_target(target)
            b.ensure_focus(editor)
            try:
                b.invoke(
                    wait_until(
                        lambda: b.by_class("send-msg"), lambda button: button.IsEnabled
                    )
                )
                messages = wait_until(
                    lambda: [
                        m
                        for m in c._read(target)
                        if m.id not in previous
                        and m.direction == "outgoing"
                        and m.text == expected_text
                        and m.quote is not None
                    ],
                    timeout=timeout,
                )
                wait_until(
                    lambda: editor_has_content(b.snapshot(b.editor())),
                    lambda value: not value,
                    timeout=timeout,
                )
            except Exception as exc:
                raise SendOutcomeUnknown(
                    "Quoted reply outcome is uncertain; inspect QQ before retrying"
                ) from exc
            return SendReceipt(target.id, messages[-1].id, expected_text)

    def media(self, key, path, kind, *, timeout=30):
        path = local_media_path(path, kind)
        c = self.client
        target = c.target(key)
        b = c.backend
        with ui_transaction():
            previous = self._prepare(target)
            existing = b.find(
                b.window,
                lambda x: (
                    x.ControlTypeName == "WindowControl"
                    and x.ClassName in ("#32770", "q-dialog")
                ),
                required=False,
            )
            if existing is not None:
                raise ControlUnavailable(
                    "An existing QQ dialog must be handled before sending media"
                )
            toolbar_name = "图片" if kind == "image" else "文件"

            def toolbar_button():
                matches = [
                    x
                    for x in b.walk(b.window)
                    if x.ControlTypeName == "ButtonControl"
                    and x.Name == toolbar_name
                    and "icon-item" in x.ClassName.split()
                    and not x.IsOffscreen
                ]
                if len(matches) != 1:
                    raise ControlUnavailable(
                        "Media toolbar button is missing or ambiguous"
                    )
                return matches[0]

            # Private QQ's primary file button selects the online transfer path.
            # Use its explicit offline menu to retain the preview/status contract.
            if kind == "file" and target.kind == "private":

                def file_arrow():
                    return b.find(
                        b.window,
                        lambda x: (
                            x.ControlTypeName == "ButtonControl"
                            and x.Name == "文件弹出菜单"
                            and "arrow" in x.ClassName.split()
                        ),
                    )

                b.focus_control(file_arrow(), resolve=file_arrow)
                b.auto.SendKeys("{Enter}", waitTime=0.1)
                menu = wait_until(
                    lambda: b.find(
                        b.window,
                        lambda x: (
                            x.ControlTypeName == "MenuControl" and not x.IsOffscreen
                        ),
                        False,
                    )
                )
                leaf = b.find(
                    menu,
                    lambda x: (
                        x.ControlTypeName == "MenuItemControl"
                        and x.Name == "发送离线文件"
                    ),
                )
                b.invoke(leaf)
            else:
                # Closing a profile popover can leave the main window inactive.
                b.focus_control(toolbar_button(), resolve=toolbar_button)
                b.auto.SendKeys("{Enter}", waitTime=0.1)
            if hasattr(b.auto, "find_file_picker"):
                picker = wait_until(lambda: b.auto.find_file_picker(b.window), timeout=30)
                try:
                    picker.set_path(path)
                    picker.confirm()
                    wait_until(lambda: b.auto.find_file_picker(b.window), lambda value: value is None)
                except Exception:
                    if b.auto.find_file_picker(b.window) is not None:
                        picker.cancel()
                    raise
            else:
                dialog = wait_until(
                    lambda: b.find(
                        b.window,
                        lambda x: (
                            x.ControlTypeName == "WindowControl" and x.ClassName == "#32770"
                        ),
                        required=False,
                    ),
                    timeout=30,
                )
                dialog_handle = dialog.NativeWindowHandle
                try:
                    edit = b.find(
                        dialog,
                        lambda x: (
                            x.ControlTypeName == "EditControl" and x.AutomationId == "1148"
                        ),
                    )
                    value = edit.GetValuePattern()
                    value.SetValue(str(path), waitTime=0.05)
                    if value.Value != str(path):
                        raise TargetMismatch("File picker path verification failed")
                    b.invoke(
                        b.find(
                            dialog,
                            lambda x: (
                                x.ControlTypeName == "ButtonControl"
                                and x.AutomationId == "1"
                                and x.ClassName == "Button"
                            ),
                        )
                    )
                    wait_until(
                        lambda: b.find(
                            b.window,
                            lambda x: (
                                x.ControlTypeName == "WindowControl"
                                and x.ClassName == "#32770"
                            ),
                            required=False,
                        ),
                        lambda x: x is None,
                    )
                except Exception:
                    # Close only the picker opened by this operation, not an existing one.
                    if (
                        b.find(
                            b.window,
                            lambda x: x.NativeWindowHandle == dialog_handle,
                            required=False,
                        )
                        is not None
                    ):
                        dialog.GetWindowPattern().Close(waitTime=0.05)
                    raise
            if kind == "file":
                preview = wait_until(
                    lambda: b.find(
                        b.window,
                        lambda x: "send-file-msg" in x.ClassName.split(),
                        required=False,
                    )
                )
                names = [
                    text_of(b.snapshot(x))
                    for x in b.walk(preview)
                    if "file-name" in x.ClassName.split()
                ]
                title = b.find(
                    preview,
                    lambda x: (
                        x.ControlTypeName == "TextControl"
                        and x.Name == f"发送给 {target.name}"
                    ),
                    required=False,
                )
                if names != [path.name] or title is None:
                    raise TargetMismatch(
                        "QQ file preview target or filename differs from the request"
                    )
                send = b.find(
                    preview,
                    lambda x: (
                        x.ControlTypeName == "ButtonControl" and x.Name == "发送(1)"
                    ),
                )
            else:
                wait_until(
                    lambda: b.find(
                        b.editor(),
                        lambda x: "editor-el--inline-block" in x.ClassName.split(),
                        required=False,
                    )
                )
                send = wait_until(lambda: b.by_class("send-msg"), lambda x: x.IsEnabled)
            self._assert_target(target)
            try:
                b.invoke(send)

                def sent():
                    return [
                        m
                        for m in c._read(target)
                        if m.id not in previous
                        and m.direction == "outgoing"
                        and m.kind == kind
                        and (
                            kind == "image"
                            or any(
                                a.name == path.name and "已发送" in (a.status or "")
                                for a in m.attachments
                            )
                        )
                    ]

                message = wait_until(sent, timeout=timeout)[-1]
                wait_until(
                    lambda: editor_has_content(b.snapshot(b.editor())),
                    lambda value: not value,
                    timeout=timeout,
                )
            except Exception as exc:
                raise SendOutcomeUnknown(
                    "Media send outcome is uncertain; inspect QQ before retrying"
                ) from exc
            return MediaReceipt(target.id, message.id, kind, path.name, str(path))
