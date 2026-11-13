import threading

import pytest

from quitbot_backend._engine.errors import FocusLost
from quitbot_backend._engine.models import Node, editor_has_content, editor_text
from quitbot_backend._engine.ui.backend import DesktopBackend


class FakeAutomation:
    def type_text(self, control, text):
        from quitbot_backend._engine.platforms.windows.desktop import type_text

        return type_text(self, control, text)

    def __init__(self, editor):
        self.focus = editor
        self.units = []
        self.keys = []

    def GetFocusedControl(self):
        return self.focus

    def ControlsAreSame(self, a, b):
        return a is b

    def SendUnicodeChar(self, char):
        self.units.append(char)
        return 1

    def SendKeys(self, key, **kwargs):
        self.keys.append(key)


def backend(editor):
    instance = DesktopBackend.__new__(DesktopBackend)
    instance.thread_id = threading.get_ident()
    instance.auto = FakeAutomation(editor)
    return instance


def test_message_text_cannot_become_key_commands_and_emoji_surrogates_work():
    editor = object()
    b = backend(editor)
    text = "中文 {Enter} {Ctrl} 😀\n第二行"
    b.type_literal(editor, text)
    encoded = "".join(b.auto.units).encode("utf-16-le", errors="surrogatepass")
    assert encoded.decode("utf-16-le") == text.replace("\n", "")
    assert b.auto.keys == ["{Shift}{Enter}"]


def test_focus_loss_stops_input():
    editor = object()
    b = backend(editor)
    b.auto.focus = object()
    with pytest.raises(FocusLost):
        b.type_literal(editor, "不应发送")
    assert b.auto.units == []


def test_empty_editor_voice_hint_is_not_a_draft_but_literal_user_text_is_preserved():
    hint = Node(type="TextControl", name="按住 Win + Alt，使用语音输入文字")
    line = Node(type="TextControl", name="\n", class_name="ProseMirror-trailingBreak")
    empty = Node(
        class_name="ProseMirror is-empty ExEditor-qq-msg-editor", children=(hint, line)
    )
    assert editor_text(empty) == "" and not editor_has_content(empty)
    actual = Node(
        class_name="ProseMirror ExEditor-qq-msg-editor", children=(hint, line)
    )
    assert editor_has_content(actual) and hint.name in editor_text(actual)
    unknown = Node(
        class_name="ProseMirror is-empty",
        children=(Node(type="TextControl", name="keep me"),),
    )
    assert editor_has_content(unknown)


def test_empty_prosemirror_structural_line_break_is_not_reported_as_draft_text():
    from quitbot_backend._engine.ui.workflows.chat import draft_state

    line = Node(type="TextControl", name="\n", class_name="ProseMirror-trailingBreak")
    editor = Node(
        class_name="ProseMirror is-empty ExEditor-qq-msg-editor", children=(line,)
    )
    assert draft_state(editor).text == "" and not editor_has_content(editor)
    literal = Node(
        class_name="ProseMirror is-empty",
        children=(Node(type="TextControl", name="keep\nme"),),
    )
    assert draft_state(literal).text == "keep\nme"
