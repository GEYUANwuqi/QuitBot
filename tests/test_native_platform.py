import builtins
import ctypes
import sys
from types import ModuleType, SimpleNamespace

import pytest

from quitbot_backend._engine import native
from quitbot_backend._engine.errors import ControlUnavailable


def test_linux_helpers_never_fall_back_to_windows_imports(monkeypatch):
    monkeypatch.setattr(native, "sys", SimpleNamespace(platform="linux"))
    original = builtins.__import__

    def checked_import(name, *args, **kwargs):
        if name.startswith(("comtypes", "uiautomation")):
            raise AssertionError("Linux attempted a Windows import")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", checked_import)
    control = SimpleNamespace(show_context_menu=lambda: "AT-SPI")
    provider = SimpleNamespace(clipboard_sequence=lambda: 17)
    assert native.show_context_menu(control) == "AT-SPI"
    assert native.clipboard_sequence(provider) == 17
    for value in (
        SimpleNamespace(),
        SimpleNamespace(show_context_menu=1, clipboard_sequence=1),
    ):
        with pytest.raises(ControlUnavailable):
            native.show_context_menu(value)
        with pytest.raises(ControlUnavailable):
            native.clipboard_sequence(value)


def test_windows_helpers_use_windows_even_if_object_has_linux_hooks(monkeypatch):
    monkeypatch.setattr(native, "sys", SimpleNamespace(platform="win32"))
    module = ModuleType("comtypes.gen.UIAutomationClient")
    module.IUIAutomationElement3 = object()
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(user32=SimpleNamespace(GetClipboardSequenceNumber=lambda: 23)),
        raising=False,
    )

    def wrong_platform():
        raise AssertionError("Windows attempted a Linux operation")

    control = SimpleNamespace(
        show_context_menu=wrong_platform,
        Element=SimpleNamespace(
            QueryInterface=lambda interface: SimpleNamespace(
                ShowContextMenu=lambda: "UIA"
            )
        ),
    )
    assert native.show_context_menu(control) == "UIA"
    assert (
        native.clipboard_sequence(SimpleNamespace(clipboard_sequence=wrong_platform))
        == 23
    )


def test_unsupported_platform_does_not_choose_either_provider(monkeypatch):
    monkeypatch.setattr(native, "sys", SimpleNamespace(platform="unsupported"))
    for operation in (native.show_context_menu, native.clipboard_sequence):
        with pytest.raises(OSError):
            operation(SimpleNamespace())
    with pytest.raises(OSError):
        native.automation()
