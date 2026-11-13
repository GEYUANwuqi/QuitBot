"""Platform operations shared by QQ's semantic UI workflows."""

import sys
import threading
from contextlib import contextmanager

from ..errors import ControlTreeChanged, ControlUnavailable

TRANSPORT_NAME = "AT-SPI" if sys.platform == "linux" else "UIA"

_local = threading.local()


def automation():
    if sys.platform == "win32":
        from .windows.automation import Automation

        return Automation()
    if sys.platform == "linux":
        from .linux.automation import Automation

        if not hasattr(_local, "automation"):
            from ..locking import ui_transaction

            with ui_transaction():
                _local.automation = Automation()
        return _local.automation
    raise OSError("QuitBot requires a Windows desktop or Linux X11 session")


def show_context_menu(control):
    if sys.platform == "linux":
        native = getattr(control, "show_context_menu", None)
        if not callable(native):
            raise ControlUnavailable(
                "Linux control has no AT-SPI context-menu operation"
            )
        return native()
    if sys.platform != "win32":
        raise OSError("Context menus require Windows or Linux")
    from .windows.desktop import show_context_menu as windows_menu

    return windows_menu(control)


def clipboard_sequence(auto):
    if sys.platform == "linux":
        native = getattr(auto, "clipboard_sequence", None)
        if not callable(native):
            raise ControlUnavailable(
                "Linux provider has no X11 clipboard notifications"
            )
        return native()
    if sys.platform != "win32":
        raise OSError("Clipboard notifications require Windows or Linux")
    from .windows.desktop import clipboard_sequence as windows_sequence

    return windows_sequence(auto)


def is_transient_error(error):
    if isinstance(error, ControlTreeChanged):
        return True
    if hasattr(error, "hresult"):
        from .windows.desktop import is_transient_error as windows_error

        return windows_error(error)
    if sys.platform == "linux":
        from .linux.errors import is_transient_error as linux_error

        return linux_error(error)
    return False


@contextmanager
def exclusive_reader(path):
    if sys.platform == "win32":
        from .windows.desktop import exclusive_reader as windows_reader

        with windows_reader(path) as stream:
            yield stream
    elif sys.platform == "linux":
        with open(path, "rb") as stream:
            yield stream
    else:
        raise OSError("File verification requires Windows or Linux")


def initialize_thread(enabled=True):
    if enabled and sys.platform == "win32":
        auto = automation()
        auto.InitializeUIAutomationInCurrentThread()
        return auto
    return None


def is_provider_error(error):
    if sys.platform == "win32":
        from comtypes import COMError

        return isinstance(error, COMError)
    if sys.platform == "linux":
        from gi.repository import GLib

        return isinstance(error, GLib.Error)
    return False


def close_automation(initialized=None):
    if initialized is not None:
        initialized.UninitializeUIAutomationInCurrentThread()
    instance = getattr(_local, "automation", None)
    if instance is not None:
        try:
            instance.desktop.close_connection()
        finally:
            del _local.automation
