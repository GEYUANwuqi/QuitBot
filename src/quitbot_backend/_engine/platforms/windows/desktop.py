"""Windows native operations used by the shared QQ UI workflows."""

import os
from contextlib import contextmanager

from ...errors import FocusLost


def show_context_menu(control):
    from comtypes.gen.UIAutomationClient import IUIAutomationElement3

    return control.Element.QueryInterface(IUIAutomationElement3).ShowContextMenu()


def clipboard_sequence(auto):
    import ctypes

    return ctypes.windll.user32.GetClipboardSequenceNumber()


def is_transient_error(error):
    return getattr(error, "hresult", None) in {-2147220991, -2147418111, -2147417846}


def type_text(auto, editor, text):
    def ensure_focus():
        current = auto.GetFocusedControl()
        if not current or not auto.ControlsAreSame(current, editor):
            raise FocusLost("QQ editor lost focus; input stopped")

    lines = text.split("\n")
    for i, line in enumerate(lines):
        if i:
            ensure_focus()
            auto.SendKeys("{Shift}{Enter}", waitTime=0.01)
        data = line.encode("utf-16-le")
        for pos in range(0, len(data), 2):
            ensure_focus()
            unit = int.from_bytes(data[pos : pos + 2], "little")
            if not auto.SendUnicodeChar(chr(unit)):
                raise FocusLost("Windows rejected Unicode keyboard input")


def native_cards(auto, pid):
    if not hasattr(auto, "ControlFromHandle"):
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    result = []

    @callback_type
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            title, cls = (
                ctypes.create_unicode_buffer(256),
                ctypes.create_unicode_buffer(256),
            )
            user32.GetWindowTextW(hwnd, title, 256)
            user32.GetClassNameW(hwnd, cls, 256)
            if title.value == "资料卡" and cls.value == "Chrome_WidgetWin_1":
                result.append((hwnd, bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(visit, 0)
    return [(auto.ControlFromHandle(hwnd), visible) for hwnd, visible in result]


@contextmanager
def exclusive_reader(path):
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        kernel.CloseHandle(handle)
        raise
    with os.fdopen(fd, "rb") as stream:
        yield stream


def is_lock_surface(control):
    if control is None:
        return False
    name = control.Name.casefold()
    cls = control.ClassName.casefold()
    return "lockscreen" in cls or (
        cls == "windows.ui.core.corewindow"
        and name
        in {"windows 默认锁屏界面", "windows default lock screen", "lock screen"}
    )

def require_unlocked(auto):
    from ...ui.workflows.inspection import DesktopLocked, DesktopUnavailable

    foreground = auto.GetForegroundControl()
    if foreground is None:
        raise DesktopUnavailable(
            "No foreground window is available; restore the interactive Windows desktop before UI input"
        )
    if is_lock_surface(foreground):
        raise DesktopLocked(
            "Windows lock screen is foreground; unlock the desktop before UI investigation"
        )
    for window in auto.GetRootControl().GetChildren():
        if is_lock_surface(window) and not window.IsOffscreen:
            raise DesktopLocked(
                "Windows lock-screen occlusion is present; no UI input performed"
            )


def copied_files():
    """Read only CF_HDROP file paths explicitly placed on the clipboard by QQ."""
    import ctypes
    from ctypes import wintypes

    from ...errors import ControlUnavailable

    user, shell = ctypes.windll.user32, ctypes.windll.shell32
    user.OpenClipboard.argtypes, user.OpenClipboard.restype = [wintypes.HWND], wintypes.BOOL
    user.GetClipboardData.argtypes, user.GetClipboardData.restype = [wintypes.UINT], wintypes.HANDLE
    user.CloseClipboard.argtypes, user.CloseClipboard.restype = [], wintypes.BOOL
    shell.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell.DragQueryFileW.restype = wintypes.UINT
    if not user.OpenClipboard(None):
        raise ControlUnavailable('无法打开 Windows 剪贴板')
    try:
        handle = user.GetClipboardData(15)  # CF_HDROP, owned by the clipboard.
        if not handle:
            raise ControlUnavailable('QQ 未向剪贴板导出原始图片文件')
        count = shell.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
        if not 1 <= count <= 8:
            raise ControlUnavailable('QQ 剪贴板文件数量异常')
        paths = []
        for index in range(count):
            size = shell.DragQueryFileW(handle, index, None, 0)
            if not 1 <= size <= 32767:
                raise ControlUnavailable('QQ 剪贴板文件路径长度异常')
            buffer = ctypes.create_unicode_buffer(size + 1)
            if shell.DragQueryFileW(handle, index, buffer, size + 1) != size:
                raise ControlUnavailable('QQ 剪贴板文件路径读取失败')
            paths.append(buffer.value)
        return paths
    finally:
        user.CloseClipboard()


def copied_rich_text():
    import ctypes
    from ctypes import wintypes

    from ...errors import ControlUnavailable

    user, kernel = ctypes.windll.user32, ctypes.windll.kernel32
    user.RegisterClipboardFormatW.argtypes, user.RegisterClipboardFormatW.restype = [wintypes.LPCWSTR], wintypes.UINT
    user.OpenClipboard.argtypes, user.OpenClipboard.restype = [wintypes.HWND], wintypes.BOOL
    user.GetClipboardData.argtypes, user.GetClipboardData.restype = [wintypes.UINT], wintypes.HANDLE
    user.CloseClipboard.argtypes, user.CloseClipboard.restype = [], wintypes.BOOL
    kernel.GlobalSize.argtypes, kernel.GlobalSize.restype = [wintypes.HANDLE], ctypes.c_size_t
    kernel.GlobalLock.argtypes, kernel.GlobalLock.restype = [wintypes.HANDLE], ctypes.c_void_p
    kernel.GlobalUnlock.argtypes, kernel.GlobalUnlock.restype = [wintypes.HANDLE], wintypes.BOOL
    if not user.OpenClipboard(None):
        raise ControlUnavailable('无法打开 Windows 剪贴板')
    try:
        handle = user.GetClipboardData(user.RegisterClipboardFormatW('QQ_Unicode_RichEdit_Format'))
        size = kernel.GlobalSize(handle) if handle else 0
        if not 0 < size <= 65536:
            raise ControlUnavailable('QQ 未提供有效的富文本剪贴板格式')
        pointer = kernel.GlobalLock(handle)
        if not pointer:
            raise ControlUnavailable('无法读取 QQ 富文本剪贴板')
        try:
            data = ctypes.string_at(pointer, size)
        finally:
            kernel.GlobalUnlock(handle)
        encoding = 'utf-16-le' if data.startswith(b'<\x00') else 'utf-8'
        return data.decode(encoding).rstrip('\x00')
    finally:
        user.CloseClipboard()
