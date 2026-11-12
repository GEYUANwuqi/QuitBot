"""GTK native file dialogs, including builds without an AT-SPI dialog tree."""

import time
import uuid

from ...errors import FocusLost, TargetMismatch


class FilePicker:
    def __init__(self, auto, window, owner):
        self.auto, self.window, self.owner = auto, window, owner
        self.NativeWindowHandle = window.id
        self.Name = window.title

    def check(self):
        current = self.auto.find_file_picker(self.owner)
        if current is None or current.window != self.window:
            raise TargetMismatch("Native file picker changed")
        if self.auto.desktop.foreground() != self.window.id:
            raise FocusLost("Native file picker lost focus")

    def copy_field(self):
        from ...uia import wait_until

        self.check()
        self.auto.desktop.set_clipboard("quitbot-clipboard-check-" + uuid.uuid4().hex)
        sequence = self.auto.clipboard_sequence()
        self.check()
        self.auto.SendKeys("{Ctrl}a{Ctrl}c")
        wait_until(lambda: self.auto.clipboard_sequence() != sequence, timeout=2)
        self.check()
        return self.auto.GetClipboardText()

    def set_path(self, path, *, expected_name=None, directory=False):
        self.auto.desktop.activate(self.window.id, self.window.pid)
        self.check()
        self.auto.SendKeys(
            "{Alt}n" if expected_name is not None else "{Ctrl}l", waitTime=0.1
        )
        if expected_name is not None and self.copy_field() != expected_name:
            raise TargetMismatch("Save picker does not identify the selected file")
        self.check()
        self.auto.SendKeys("{Ctrl}a")
        self.auto.desktop.set_clipboard(str(path))
        self.check()
        self.auto.SendKeys("{Ctrl}v", waitTime=0.1)
        copied = self.copy_field()
        expected = str(path)
        if directory:
            copied, expected = copied.rstrip("/"), expected.rstrip("/")
        if copied != expected:
            raise TargetMismatch("Native picker path verification failed")

    def confirm(self):
        self.check()
        self.auto.SendKeys("{Enter}", waitTime=0.15)

    def cancel(self):
        self.auto.desktop.close(self.window.id, self.window.pid)
        time.sleep(0.1)
