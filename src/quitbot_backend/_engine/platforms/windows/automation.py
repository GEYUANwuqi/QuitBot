"""Windows control provider with the same operation surface as Linux."""

from . import desktop
from .file_picker import find_file_picker


class Automation:
    file_sort_keys = ("newest", "oldest", "size_desc", "size_asc")
    input_backend = "UIA focus + Unicode keyboard input"

    def __init__(self):
        import uiautomation

        self.controls = uiautomation

    def __getattr__(self, name):
        return getattr(self.controls, name)

    def type_text(self, control, text):
        return desktop.type_text(self, control, text)

    def file_picker(self, backend, owner):
        return find_file_picker(backend, owner)

    def copied_rich_text(self):
        return desktop.copied_rich_text()

    def copied_files(self):
        return desktop.copied_files()

    def native_cards(self, pid):
        return desktop.native_cards(self, pid)

    def require_unlocked(self):
        return desktop.require_unlocked(self)

    def is_lock_surface(self, control):
        return desktop.is_lock_surface(control)
