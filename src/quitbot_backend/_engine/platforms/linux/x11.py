"""X11 window identity, focus, clipboard and XTEST input."""

import io
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass

from ...errors import ControlUnavailable, FocusLost


@dataclass(frozen=True)
class Window:
    id: int
    pid: int
    title: str
    class_name: str
    bounds: tuple[int, int, int, int]
    mapped: bool


class Bitmap:
    def __init__(self, image):
        self.image = image

    def ToBytes(self, format):
        if format == "png":
            output = io.BytesIO()
            self.image.save(output, format="PNG")
            return output.getvalue()
        if format == "bgra":
            return self.image.convert("RGBA").tobytes("raw", "BGRA")
        raise ValueError(f"Unknown bitmap format: {format}")


class Desktop:
    def __init__(self):
        from Xlib import X, display
        from Xlib.ext import xfixes

        if not os.environ.get("DISPLAY"):
            raise ControlUnavailable("Linux QQ 需要 DISPLAY 指向持续运行的 X11 会话")
        for command in ("xdotool", "xclip"):
            if shutil.which(command) is None:
                raise ControlUnavailable(f"Linux QQ 缺少依赖：{command}")
        try:
            self.display = display.Display()
        except Exception as error:
            raise ControlUnavailable(
                "无法连接 X11，请检查 DISPLAY 与 XAUTHORITY"
            ) from error
        self.root = self.display.screen().root
        self._atoms = {}
        self._sequence = 0
        self._clipboard_window = self.root.create_window(
            0, 0, 1, 1, 0, X.CopyFromParent, X.InputOnly, X.CopyFromParent
        )
        if not self.display.has_extension("XFIXES"):
            raise ControlUnavailable("X11 服务缺少 XFIXES 剪贴板变更通知")
        self.display.xfixes_query_version()
        self.display.xfixes_select_selection_input(
            self._clipboard_window,
            self.atom("CLIPBOARD"),
            xfixes.XFixesSetSelectionOwnerNotifyMask
            | xfixes.XFixesSelectionWindowDestroyNotifyMask
            | xfixes.XFixesSelectionClientCloseNotifyMask,
        )
        self.display.sync()

    def close_connection(self):
        self._clipboard_window.destroy()
        self.display.close()

    def atom(self, name):
        if name not in self._atoms:
            self._atoms[name] = self.display.intern_atom(name)
        return self._atoms[name]

    def property(self, window, name):
        from Xlib import X

        value = window.get_full_property(self.atom(name), X.AnyPropertyType)
        return value.value if value is not None else None

    def windows(self):
        from Xlib import X, error

        result, seen = [], set()
        # Unmapped/override-redirect popovers are absent from _NET_CLIENT_LIST.
        pending = [(self.root, 0)]
        while pending:
            parent, depth = pending.pop()
            try:
                children = parent.query_tree().children
            except error.XError:
                continue
            for window in children:
                if window.id in seen:
                    continue
                seen.add(window.id)
                try:
                    pid = self.property(window, "_NET_WM_PID")
                    if pid is not None and len(pid):
                        geometry = window.get_geometry()
                        point = self.root.translate_coords(window, 0, 0)
                        title = self.property(window, "_NET_WM_NAME")
                        if isinstance(title, bytes):
                            title = title.decode("utf-8", "replace")
                        if not isinstance(title, str):
                            title = window.get_wm_name() or ""
                        classes = window.get_wm_class() or ()
                        result.append(
                            Window(
                                window.id,
                                int(pid[0]),
                                title,
                                " ".join(classes),
                                (point.x, point.y, geometry.width, geometry.height),
                                window.get_attributes().map_state == X.IsViewable,
                            )
                        )
                    if depth < 2:
                        pending.append((window, depth + 1))
                except (error.XError, UnicodeError):
                    continue
        return result

    def window(self, handle, pid=None):
        values = [
            w
            for w in self.windows()
            if w.id == handle and (pid is None or w.pid == pid)
        ]
        if len(values) != 1:
            raise ControlUnavailable("X11 窗口已关闭或被其他进程替换")
        return values[0]

    def run(self, *arguments):
        try:
            return subprocess.check_output(
                ["xdotool", *map(str, arguments)],
                text=True,
                stderr=subprocess.PIPE,
                timeout=5,
            ).strip()
        except (OSError, subprocess.SubprocessError) as error:
            raise ControlUnavailable(f"X11 操作失败：{arguments[0]}") from error

    def foreground(self):
        value = self.property(self.root, "_NET_ACTIVE_WINDOW")
        return int(value[0]) if value is not None and len(value) else 0

    def activate(self, handle, pid):
        self.window(handle, pid)
        self.run("windowactivate", "--sync", handle)
        # The WM can publish _NET_ACTIVE_WINDOW just after the focus event that
        # xdotool waits for, especially when restoring a minimized window.
        deadline = time.monotonic() + 2
        while self.foreground() != handle:
            if time.monotonic() >= deadline:
                raise FocusLost("X11 未激活已核验的 QQ 窗口")
            time.sleep(0.02)

    def close(self, handle, pid):
        from Xlib import X, protocol

        self.window(handle, pid)
        window = self.display.create_resource_object("window", handle)
        event = protocol.event.ClientMessage(
            window=window,
            client_type=self.atom("WM_PROTOCOLS"),
            data=(32, [self.atom("WM_DELETE_WINDOW"), X.CurrentTime, 0, 0, 0]),
        )
        window.send_event(event, event_mask=0)
        self.display.flush()

    def clipboard_sequence(self):
        self.display.sync()
        while self.display.pending_events():
            event = self.display.next_event()
            if getattr(event, "selection", None) == self.atom("CLIPBOARD"):
                self._sequence += 1
        return self._sequence

    def copied_rich_text(self):
        try:
            data = subprocess.check_output(
                ["xclip", "-selection", "clipboard", "-o", "-target", "QQ_Unicode_RichEdit_Format"],
                stderr=subprocess.PIPE, timeout=3,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ControlUnavailable("QQ 未提供富文本剪贴板格式") from error
        if len(data) > 65536:
            raise ControlUnavailable("QQ 富文本剪贴板内容过大")
        return data.decode("utf-8")

    def copied_files(self):
        from urllib.parse import unquote, urlsplit

        try:
            value = subprocess.check_output(
                ["xclip", "-selection", "clipboard", "-o", "-target", "x-special/gnome-copied-files"],
                stderr=subprocess.PIPE, timeout=3,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ControlUnavailable("QQ 未向剪贴板导出原始图片文件") from error
        if len(value) > 65536:
            raise ControlUnavailable("QQ 剪贴板文件列表过大")
        lines = value.decode("utf-8").splitlines()
        if not lines or lines[0] != "copy":
            raise ControlUnavailable("QQ 剪贴板不是文件复制结果")
        paths = []
        for line in lines[1:]:
            uri = urlsplit(line)
            if uri.scheme != "file" or uri.netloc not in ("", "localhost") or uri.query or uri.fragment:
                raise ControlUnavailable("QQ 剪贴板资源不是本地文件")
            paths.append(unquote(uri.path))
        return paths

    def clipboard_text(self):
        try:
            return subprocess.check_output(
                ["xclip", "-selection", "clipboard", "-o"],
                text=True,
                stderr=subprocess.PIPE,
                timeout=3,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ControlUnavailable("无法读取 X11 剪贴板文本") from error

    def set_clipboard(self, text):
        try:
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text,
                text=True,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ControlUnavailable("无法写入 X11 剪贴板") from error
        deadline = time.monotonic() + 2
        while self.clipboard_text() != text:
            if time.monotonic() >= deadline:
                raise ControlUnavailable("X11 剪贴板写入未确认")
            time.sleep(0.01)

    def send_keys(self, keys):
        names = {
            "Ctrl": "ctrl",
            "Shift": "shift",
            "Alt": "alt",
            "Win": "super",
            "Enter": "Return",
            "Esc": "Escape",
            "Back": "BackSpace",
            "Space": "space",
        }
        modifiers, strokes = [], []
        for match in re.finditer(r"\{([^{}]+)\}|([^{}])", keys):
            token = match.group(1) or match.group(2)
            if token in ("Ctrl", "Shift", "Alt", "Win"):
                modifiers.append(names[token])
            else:
                if match.group(2) and not token.isascii():
                    raise ValueError(
                        "Literal Unicode input must use type_text"
                    )
                strokes.append("+".join([*modifiers, names.get(token, token)]))
                modifiers.clear()
        if modifiers or not strokes:
            raise ValueError("Incomplete keyboard chord")
        self.run("key", "--clearmodifiers", *strokes)

    def type_text(self, text):
        if "\n" in text or "\r" in text:
            raise ValueError("Line breaks require an explicit editor newline chord")
        try:
            subprocess.run(
                ["xdotool", "type", "--clearmodifiers", "--delay", "12", "--file", "-"],
                input=text,
                text=True,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ControlUnavailable("X11 Unicode keyboard input failed") from error

    def cursor(self):
        point = self.root.query_pointer()
        return point.root_x, point.root_y

    def move_cursor(self, x, y):
        if self.cursor() != (x, y):
            self.run("mousemove", "--sync", x, y)
        return self.cursor()

    def capture(self, bounds):
        from PIL import ImageGrab

        left, top, width, height = bounds
        return Bitmap(
            ImageGrab.grab(
                bbox=(left, top, left + width, top + height),
                xdisplay=os.environ["DISPLAY"],
            )
        )

    def move(self, handle, pid, x, y):
        self.window(handle, pid)
        self.run("windowmove", handle, x, y)

    @staticmethod
    def is_lock_window(window):
        return window is not None and window.mapped and any(
            word in window.class_name.lower()
            for word in ("screensaver", "screenlocker", "lockscreen", "kscreenlocker")
        )

    def ensure_unlocked(self):
        from ...ui.workflows.inspection import DesktopLocked

        for window in self.windows():
            if self.is_lock_window(window):
                raise DesktopLocked("请先解锁 Linux 图形会话")
        if not self.foreground():
            # X11 permits an idle desktop with no active window; activation is
            # verified separately for every input operation.
            return
