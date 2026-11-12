"""Platform operations shared by QQ's semantic UI workflows."""

import sys
import threading

from .errors import ControlUnavailable

TRANSPORT_NAME = "AT-SPI" if sys.platform == "linux" else "UIA"

_local = threading.local()


def automation():
    if sys.platform == "win32":
        import uiautomation

        return uiautomation
    if sys.platform == "linux":
        from .infrastructure.atspi.automation import Automation

        if not hasattr(_local, "automation"):
            from .locking import ui_transaction

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
    from comtypes.gen.UIAutomationClient import IUIAutomationElement3

    return control.Element.QueryInterface(IUIAutomationElement3).ShowContextMenu()


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
    import ctypes

    return ctypes.windll.user32.GetClipboardSequenceNumber()


def close_automation():
    instance = getattr(_local, "automation", None)
    if instance is not None:
        try:
            instance.desktop.close_connection()
        finally:
            del _local.automation
