"""Send a named default QQ face using its native UIA picker control."""

from ...domain.faces import FaceReceipt
from ...errors import (
    ControlUnavailable,
    DraftNotEmpty,
    SendOutcomeUnknown,
    TargetMismatch,
)
from ...models import editor_has_content, editor_text
from ...operations import validate_text
from ...uia import wait_until


class FacesUI:
    def __init__(self, chat):
        self.ui, self.b, self.client = chat, chat.b, chat.client

    def send(self, key, label, *, text=""):
        if (
            not isinstance(label, str)
            or not label
            or len(label) > 32
            or any(ord(c) < 32 for c in label)
        ):
            raise ValueError("Face label must be 1..32 characters")
        if not isinstance(text, str):
            raise TypeError("Face caption must be a string")
        if text:
            validate_text(text)
        with self.ui.checked(key) as target:
            if editor_has_content(self.b.snapshot(self.b.editor())):
                raise DraftNotEmpty("Existing composer draft retained")
            if self.ui.by_class("sticker-panel") is not None:
                raise ControlUnavailable("Existing expression panel retained")
            previous = {m.id for m in self.client._read(target)}
            button = self.b.find(
                self.b.window,
                lambda c: c.ControlTypeName == "ButtonControl" and c.Name == "表情",
            )
            self.b.invoke(button)
            original = None
            try:

                def tabs():
                    return [
                        c
                        for c in self.b.walk(self.b.window)
                        if c.ControlTypeName == "ButtonControl"
                        and "tabs-container-item" in c.ClassName.split()
                    ]

                choices = wait_until(tabs)
                selected = [
                    c.Name for c in choices if c.GetTogglePattern().ToggleState == 1
                ]
                if len(selected) != 1:
                    raise ControlUnavailable(
                        "Expression category selection is ambiguous"
                    )
                original = selected[0]
                default = next((c for c in choices if c.Name == "默认表情"), None)
                if default is None:
                    raise ControlUnavailable("Default QQ face category unavailable")
                if default.GetTogglePattern().ToggleState != 1:
                    default.GetTogglePattern().Toggle(waitTime=0.1)
                pane = wait_until(
                    lambda: self.b.find(
                        self.b.window,
                        lambda c: (
                            c.ControlTypeName == "PaneControl"
                            and c.Name == "默认表情列表"
                        ),
                        False,
                    )
                )
                matches = [
                    c
                    for c in self.b.walk(pane)
                    if c.ControlTypeName == "GroupControl"
                    and c.Name == label
                    and c.ClassName == "sticker"
                ]
                if len(matches) != 1 or not matches[0].IsEnabled:
                    raise ControlUnavailable(
                        "Face label is not a unique available default-picker item"
                    )
                self.b.invoke(matches[0])
                wait_until(
                    lambda: sum(
                        n.has_class("editor-el__qqface-wrapper")
                        for n in self.b.snapshot(self.b.editor()).walk()
                    ),
                    lambda count: count == 1,
                )
            finally:
                if original and original != "默认表情":
                    if self.ui.by_class("sticker-panel") is None:
                        self.b.invoke(button)
                    tab = wait_until(
                        lambda: self.b.find(
                            self.b.window,
                            lambda c: (
                                "tabs-container-item" in c.ClassName.split()
                                and c.Name == original
                            ),
                            False,
                        )
                    )
                    if tab.GetTogglePattern().ToggleState != 1:
                        tab.GetTogglePattern().Toggle(waitTime=0.1)
                if self.ui.by_class("sticker-panel") is not None:
                    self.b.focus_control(button)
                    self.b.auto.SendKeys("{Esc}", waitTime=0.1)
            editor = self.b.focus_editor()
            if editor_text(self.b.snapshot(editor)).replace("\u200b", "").strip():
                raise TargetMismatch("Unexpected text in the face draft")
            if text:
                self.b.type_literal(editor, text)

            def valid_draft():
                node = self.b.snapshot(editor)
                return (
                    editor_text(node).replace("\u200b", "").strip("\n") == text
                    and sum(
                        n.has_class("editor-el__qqface-wrapper") for n in node.walk()
                    )
                    == 1
                    and not any(
                        n.has_class("editor-el--inline-block")
                        and not n.has_class("editor-el__qqface-wrapper")
                        for n in node.walk()
                    )
                )

            wait_until(valid_draft)
            if self.b.header().Name != target.name:
                raise TargetMismatch("Conversation changed before face send")
            self.b.ensure_focus(editor)
            send = self.b.by_class("send-msg")
            if not send.IsEnabled:
                raise ControlUnavailable("Send is disabled")
            try:
                self.b.invoke(send)
                matches = wait_until(
                    lambda: [
                        m
                        for m in self.client._read(target)
                        if m.id not in previous
                        and m.direction == "outgoing"
                        and m.text == text
                        and m.face_count == 1
                    ]
                )
                if len(matches) != 1:
                    raise TargetMismatch("Face send produced ambiguous new messages")
                wait_until(
                    lambda: editor_has_content(self.b.snapshot(self.b.editor())),
                    lambda present: not present,
                )
            except Exception as error:
                raise SendOutcomeUnknown(
                    "Face send outcome uncertain; inspect before retrying"
                ) from error
            return FaceReceipt(target.id, matches[0].id, label, text)
