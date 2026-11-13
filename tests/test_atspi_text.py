from types import SimpleNamespace

import pytest

pytest.importorskip("gi")
from quitbot_backend._engine.errors import ControlUnavailable
from quitbot_backend._engine.platforms.linux import text as native


def accessible(path, text, children=()):
    return SimpleNamespace(
        path=path, app=SimpleNamespace(bus_name=":1.42"), text=text, children=children,
        clear_cache=lambda: None, get_interfaces=lambda: ["Text", "Hypertext"],
    )


def install_text_interfaces(monkeypatch):
    monkeypatch.setattr(native.Atspi.Text, "get_text", lambda obj, start, end: obj.text)
    monkeypatch.setattr(
        native.Atspi.Hypertext, "get_link_index",
        lambda obj, offset: obj.text[:offset].count("\ufffc") if obj.children else -1,
    )
    monkeypatch.setattr(native.Atspi.Hypertext, "get_link", lambda obj, index: obj.children[index])
    monkeypatch.setattr(native.Atspi.Hyperlink, "get_object", lambda link, index: link)


def test_embedded_paragraphs_preserve_order_newlines_and_literal_replacement_character(monkeypatch):
    install_text_interfaces(monkeypatch)
    first = accessible("/first", "中文🙂")
    second = accessible("/second", "literal \ufffc")
    paragraph = accessible("/paragraph", "\ufffc\n", (first,))
    editor = accessible("/editor", "prefix \ufffc\ufffc suffix", (paragraph, second))
    assert native.text_value(editor) == "prefix 中文🙂\nliteral \ufffc suffix"


def test_cyclic_or_unreadable_embedded_text_is_rejected(monkeypatch):
    install_text_interfaces(monkeypatch)
    editor = accessible("/editor", "\ufffc")
    editor.children = (editor,)
    with pytest.raises(ControlUnavailable, match="Cyclic"):
        native.text_value(editor)
    child = accessible("/image", "")
    child.get_interfaces = lambda: ["Component"]
    editor.children = (child,)
    with pytest.raises(ControlUnavailable, match="completely"):
        native.text_value(editor)
