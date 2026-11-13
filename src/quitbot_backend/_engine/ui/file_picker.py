"""File-dialog operations shared by chat, notices, stickers and group files."""

from ..errors import ControlUnavailable


def selected_filename(label):
    """Interpret the browser's native file-input label without guessing emptiness."""
    for prefix, empty in (
        ("选择文件: ", "未选择任何文件"),
        ("Choose File: ", "No file chosen"),
    ):
        if label.startswith(prefix):
            value = label[len(prefix):]
            if value == empty:
                return None
            if value:
                return value
    raise ControlUnavailable("Unrecognized native file-input label")


def find_file_picker(backend, owner):
    return backend.auto.file_picker(backend, owner)
