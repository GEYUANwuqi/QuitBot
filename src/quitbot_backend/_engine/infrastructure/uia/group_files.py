"""Group-file operations using observed QQ controls and exact visible identity."""

import hashlib
import os
import re
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path

from quitbot_backend._engine.domain.group_files import (
    GroupFileDownloadReceipt,
    GroupFileEntry,
    GroupFileListing,
    GroupFileMutationReceipt,
)
from quitbot_backend._engine.errors import ControlUnavailable, TargetMismatch
from quitbot_backend._engine.locking import ui_transaction
from quitbot_backend._engine.models import text_of
from quitbot_backend._engine.uia import wait_until

from .inspection import hover_control
from .moderation import mutation_guard


def file_name(value):
    """Require one ordinary Windows filename, never a path or shell expression."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 200
        or value != value.strip()
        or value.endswith(".")
        or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in value)
        or value in (".", "..")
        or re.fullmatch(
            r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", value, re.IGNORECASE
        )
    ):
        raise ValueError("Provide a single ordinary file or folder name")
    return value


def parse_file_entry(node):
    """Identify folders by the observed count label, not filename extensions."""
    if not node.has_class("file-container"):
        raise ControlUnavailable("Not a group-file row")
    nodes = tuple(node.walk())
    names = [text_of(n) for n in nodes if n.has_class("file-name")]
    if len(names) != 1 or not names[0]:
        raise ControlUnavailable("File row name is missing or ambiguous")
    details = [text_of(n) for n in nodes if n.has_class("file-details-info")]
    count = re.fullmatch(r"\s*(\d+)\s*个文件\s*", details[0]) if details else None
    size = (
        details[0]
        if details and re.fullmatch(r"\s*[\d,.]+\s*(?:Bytes|[KMGT]?B)\s*", details[0])
        else None
    )
    uploader = [text_of(n) for n in nodes if n.has_class("file-send-desc")]
    dates = [
        n.name
        for n in nodes
        if n.type == "TextControl"
        and re.fullmatch(
            r"(?:\d{1,4}[/-]\d{1,2}(?:[/-]\d{1,2})?|\d{1,2}:\d{2})", n.name
        )
    ]
    return GroupFileEntry(
        name=names[0],
        kind="folder" if count else "file" if size else "unknown",
        size_text=size,
        count=int(count[1]) if count else None,
        uploader_text=uploader[0] if len(uploader) == 1 else None,
        date_text=dates[0] if len(dates) == 1 else None,
        usage_text=details[1] if len(details) > 1 else None,
    )


def unique_file_entry(entries, name, *, kind=None, expected=None):
    """Names remain local selectors; duplicates never silently choose a row."""
    matches = [entry for entry in entries if entry.name == name]
    if len(matches) != 1:
        raise ControlUnavailable("Exact file name is not uniquely loaded")
    entry = matches[0]
    if kind is not None and entry.kind != kind:
        raise TargetMismatch("File row kind differs from the expected kind")
    if expected is not None:
        if not isinstance(expected, GroupFileEntry) or expected.name != name:
            raise ValueError("expected must describe the requested file name")
        # Download counts and dates can change concurrently; require only supplied
        # stable UI metadata, never a synthetic server identity.
        for field in ("kind", "size_text", "uploader_text"):
            value = getattr(expected, field)
            if value is not None and value != getattr(entry, field):
                raise TargetMismatch("File row metadata changed since selection")
    return entry


def require_loaded_root(node, group_name):
    """Accept only the exact observed root header; unknown navigation fails closed."""
    headers = [n for n in node.walk() if n.has_class("file-list-header-container")]
    areas = [n for n in node.walk() if n.has_class("file-area-container")]
    titles = [
        text_of(n)
        for area in areas
        for n in area.children
        if n.has_class("text-ellipsis")
    ]
    searches = [
        n for n in node.walk() if n.type == "EditControl" and n.name == "搜索本群文件"
    ]
    if (
        len(areas) != 1
        or titles != [f"群文件 - {group_name}"]
        or len(searches) != 1
        or len(headers) != 1
        or len(headers[0].children) != 1
        or not headers[0]
        .children[0]
        .has_class("file-list-header-container-pull-down-group")
        or any(n.type == "TextControl" and n.name for n in headers[0].walk())
    ):
        raise ControlUnavailable(
            "Group file operations require the observed root list; nested navigation is unsupported"
        )


@contextmanager
def _exclusive_reader(path):
    """Read a local result only when QQ has released its writer handle."""
    if sys.platform != "win32":
        with path.open("rb") as stream:
            yield stream
        return
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


def downloaded_file_state(path, size_text=None):
    """Return a local content fingerprint; it is not a QQ server checksum."""
    expected_size = None
    if size_text:
        match = re.fullmatch(r"\s*([\d,]+)(?:\.0+)?\s*(?:B|Bytes)\s*", size_text)
        if match:
            expected_size = int(match[1].replace(",", ""))
    try:
        with _exclusive_reader(path) as stream:
            digest = hashlib.sha256()
            size = 0
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(block)
                digest.update(block)
    except OSError as error:
        if isinstance(error, (FileNotFoundError, PermissionError)) or getattr(
            error, "winerror", None
        ) in (2, 3, 5, 32, 33):
            return None
        raise
    if expected_size is not None and size != expected_size:
        return None
    return size, digest.hexdigest()


class GroupFilesUI:
    def __init__(self, client):
        self.client = client
        self.ui = client.control_ui
        self.secondary = self.ui.secondary
        self.b = client.backend

    def _rows(self, window):
        return [
            row
            for row in self.b.walk(window)
            if "file-container" in row.ClassName.split()
        ]

    def _entries(self, window):
        return tuple(self._read_rows(window)[1])

    def _read_rows(self, window):
        def read():
            rows = self._rows(window)
            return rows, [parse_file_entry(self.b.snapshot(row)) for row in rows]

        # Returning from a folder can replace rows after the title has changed.
        # Reacquire the whole read on transient COM errors, including empty lists.
        return wait_until(read)

    def _button(self, root, name, *, require_enabled=True):
        matches = [
            c
            for c in self.b.walk(root)
            if c.ControlTypeName == "ButtonControl"
            and c.Name == name
            and not c.IsOffscreen
        ]
        if len(matches) != 1 or require_enabled and not matches[0].IsEnabled:
            raise ControlUnavailable(f"Button {name!r} is unavailable or ambiguous")
        return matches[0]

    def _dialog(self, window):
        matches = [
            c
            for c in self.b.walk(window)
            if "q-dialog-main" in c.ClassName.split() and not c.IsOffscreen
        ]
        if len(matches) > 1:
            raise ControlUnavailable("Multiple group-file dialogs are present")
        return matches[0] if matches else None

    def _picker(self, window):
        if hasattr(self.b.auto, "find_file_picker"):
            return self.b.auto.find_file_picker(window)
        matches = [
            c
            for c in self.b.walk(window)
            if c.ControlTypeName == "WindowControl" and c.ClassName == "#32770"
        ]
        if len(matches) > 1:
            raise ControlUnavailable("Multiple native file dialogs are present")
        return matches[0] if matches else None

    def _cancel_picker(self, picker):
        if hasattr(picker, "cancel"):
            picker.cancel()
        else:
            self.b.invoke(self.b.find(picker, lambda c: c.ControlTypeName == "ButtonControl" and c.AutomationId == "2" and c.ClassName == "Button"))

    def _require_clean(self, window, target):
        if self.ui.by_class("q-dialog-main", window) is not None or self._picker(
            window
        ):
            raise ControlUnavailable("Close the existing file dialog before continuing")

        # A fresh QQ window can publish title/search before its root header.
        # Retry only the root read; a persistent unknown/nested page stays refused.
        def root_ready():
            try:
                require_loaded_root(self.b.snapshot(window), target.name)
            except ControlUnavailable:
                return False
            return True

        wait_until(root_ready)
        # Ordinary members can read/download while management buttons are hidden.
        # Mutating methods validate their own enabled action after this root guard.
        wait_until(
            lambda: (
                bool(self._rows(window))
                or self.b.find(
                    window,
                    lambda c: (
                        c.ControlTypeName == "TextControl"
                        and (
                            bool(re.fullmatch(r"共\s*\d+\s*个文件", c.Name))
                            or c.Name == "暂无文件"
                        )
                    ),
                    False,
                )
                is not None
            )
        )

    def _folder_title(self, window):
        area = self.ui.by_class("file-area-container", window, required=True)
        titles = [
            c for c in area.GetChildren() if "text-ellipsis" in c.ClassName.split()
        ]
        if len(titles) != 1:
            raise ControlUnavailable("File page title is missing or ambiguous")
        return text_of(self.b.snapshot(titles[0]))

    def _return_root(self, window, target, folder):
        title = self._folder_title(window)
        if title == f"群文件 - {target.name}":
            return
        if title != folder:
            raise TargetMismatch(
                "Folder view changed; refusing an unverified back action"
            )
        area = self.ui.by_class("file-area-container", window, required=True)
        controls = [c for c in area.GetChildren() if c.ClassName == "q-svg-icon q-icon"]
        if len(controls) != 1:
            raise ControlUnavailable(
                "Observed folder return control is missing or ambiguous"
            )
        self.b.invoke(controls[0])
        wait_until(
            lambda: self._folder_title(window),
            lambda value: value == f"群文件 - {target.name}",
        )

    @contextmanager
    def _folder(self, window, target, name):
        rows, entries = self._read_rows(window)
        entry = unique_file_entry(entries, name, kind="folder")
        row = rows[entries.index(entry)]
        try:
            self.b.invoke(row)
            wait_until(lambda: self._folder_title(window), lambda value: value == name)
            wait_until(
                lambda: (
                    bool(self._rows(window))
                    or self.b.find(
                        window,
                        lambda c: (
                            c.ControlTypeName == "TextControl" and c.Name == "暂无文件"
                        ),
                        False,
                    )
                    is not None
                )
            )
            if (
                self.b.find(
                    window,
                    lambda c: (
                        c.ControlTypeName == "EditControl" and c.Name == "搜索本群文件"
                    ),
                    False,
                )
                is not None
            ):
                raise TargetMismatch("Folder page still exposes the root search")
            yield
        finally:
            self._return_root(window, target, name)

    def file_entries(self, key, *, folder=None):
        if folder is not None:
            folder = file_name(folder)
        with ui_transaction(), self.secondary.files_view(key) as (target, window):
            self._require_clean(window, target)
            if folder is not None:
                with self._folder(window, target, folder):
                    return GroupFileListing(
                        target.id,
                        self._entries(window),
                        scope="loaded_folder",
                        folder=folder,
                    )
            return GroupFileListing(target.id, self._entries(window))

    def _require_absent(self, window, name):
        if any(
            entry.name.casefold() == name.casefold() for entry in self._entries(window)
        ):
            raise ControlUnavailable(
                "A file or folder with this name is already loaded"
            )

    @contextmanager
    def _fresh_view(self, key):
        with ui_transaction():
            pid = self.b.main_window.ProcessId
            if any(
                w.ProcessId == pid and w.Name == "群文件"
                for w in self.b.auto.GetRootControl().GetChildren()
            ):
                raise ControlUnavailable(
                    "Existing group-file window retained; close it first"
                )
            with self.secondary.files_view(key) as (target, window):
                self._require_clean(window, target)
                yield target, window

    def folder_properties(self, key, name, *, expected=None):
        name = file_name(name)
        with (
            self._fresh_view(key) as (target, window),
            self._folder_menu(window, name, expected=expected) as (menu, before),
        ):
            self.b.invoke(self.ui._menu_item(menu, "文件夹属性"))
            panel = wait_until(lambda: self.ui.by_class("attribute-panel", window))
            if (
                text_of(
                    self.b.snapshot(
                        self.ui.by_class("folder-name", panel, required=True)
                    )
                )
                != name
            ):
                raise TargetMismatch("Folder properties identify another folder")
            fields = tuple(
                c.Name
                for c in self.b.walk(panel)
                if c.ControlTypeName == "TextControl" and c.Name
            )
            return {"group_id": target.id, "entry": before, "fields": fields}

    def download_files(self, key, names, destination, *, expected=None):
        """Select exact root files and save together to a new, empty directory."""
        if isinstance(names, str):
            raise TypeError("names must be a sequence")
        names = tuple(file_name(n) for n in names)
        if not 2 <= len(names) <= 20 or len({n.casefold() for n in names}) != len(
            names
        ):
            raise ValueError("Provide 2..20 distinct file names")
        requested = Path(destination).expanduser()
        file_name(requested.name)
        path = requested.parent.resolve(strict=True) / requested.name
        if path.exists() or path.is_symlink():
            raise FileExistsError(path)
        expected = expected or {}
        submitted = [False]
        with mutation_guard(submitted), self._fresh_view(key) as (target, window):
            from quitbot_backend._engine.native import show_context_menu

            rows, entries = self._read_rows(window)
            chosen = [
                unique_file_entry(entries, n, kind="file", expected=expected.get(n))
                for n in names
            ]
            row = rows[entries.index(chosen[0])]
            show_context_menu(row)
            try:
                self.b.invoke(
                    self.ui._menu_item(wait_until(self.ui.navigation._menu), "多选")
                )
                wait_until(lambda: self.ui.by_class("forward-ops", window))
                for entry in chosen[1:]:
                    rows, entries = self._read_rows(window)
                    current = unique_file_entry(
                        entries, entry.name, kind="file", expected=entry
                    )
                    checkbox = self.b.find(
                        rows[entries.index(current)],
                        lambda c: c.ControlTypeName == "CheckBoxControl",
                    )
                    if checkbox.GetTogglePattern().ToggleState != 0:
                        raise TargetMismatch("Unexpected preselected file")
                    checkbox.GetTogglePattern().Toggle(waitTime=0.1)
                    wait_until(
                        lambda checkbox=checkbox: (
                            checkbox.GetTogglePattern().ToggleState
                        ),
                        lambda v: v == 1,
                    )
                rows, entries = self._read_rows(window)
                selected = [
                    e.name
                    for row, e in zip(rows, entries)
                    if any(
                        c.ControlTypeName == "CheckBoxControl"
                        and c.GetTogglePattern().ToggleState == 1
                        for c in self.b.walk(row)
                    )
                ]
                if len(selected) != len(names) or set(selected) != set(names):
                    raise TargetMismatch("Exact batch selection could not be confirmed")
                toolbar = self.ui.by_class("forward-ops", window, required=True)
                action = self.b.find(
                    toolbar,
                    lambda c: (
                        c.ControlTypeName == "ListItemControl" and (c.Name or text_of(self.b.snapshot(c))) == "另存为"
                    ),
                )
                icon = self.ui.by_class("op-icon", action, required=True)
                if "disabled" in icon.ClassName.split():
                    raise ControlUnavailable("Batch save is disabled")
                self.b.invoke(icon)
                picker = wait_until(lambda: self._picker(window))
                try:
                    path.mkdir(exist_ok=False)
                    if hasattr(picker, "set_path"):
                        picker.set_path(path, directory=True)
                        if any(path.iterdir()):
                            raise TargetMismatch("Batch destination changed")
                        submitted[0] = True
                        picker.confirm()
                    else:
                        field = self.b.find(
                            picker,
                            lambda c: (
                                c.AutomationId == "1152"
                                and c.ControlTypeName == "EditControl"
                            ),
                        )
                        field.GetValuePattern().SetValue(str(path), waitTime=0.1)
                        if field.GetValuePattern().Value != str(path) or any(
                            path.iterdir()
                        ):
                            raise TargetMismatch("Batch destination changed")
                        confirm = self.b.find(
                            picker,
                            lambda c: (
                                c.AutomationId == "1"
                                and c.ControlTypeName == "ButtonControl"
                                and c.ClassName == "Button"
                                and c.Name == "另存为"
                            ),
                        )
                        submitted[0] = True
                        self.b.invoke(confirm)
                    wait_until(lambda: self._picker(window), lambda p: p is None)
                    return self._download_receipts(target, chosen, path)
                finally:
                    current = self._picker(window)
                    if current is not None:
                        self._cancel_picker(current)
            finally:
                self.ui._dismiss_menu()
                if self.ui.by_class("forward-ops", window) is not None:
                    close = self.ui.by_class("close-btn", window, required=True)
                    self.b.invoke(close)
                    wait_until(
                        lambda: self.ui.by_class("forward-ops", window),
                        lambda c: c is None,
                    )

    def _download_receipts(self, target, entries, directory):
        def states():
            return [
                downloaded_file_state(directory / e.name, e.size_text) for e in entries
            ]

        values = wait_until(
            states, lambda xs: all(v is not None for v in xs), timeout=45
        )
        # Recheck after the exclusive reads so a transient zero-length file cannot
        # be accepted while QQ is still replacing it.
        values = wait_until(states, lambda xs: xs == values, timeout=5)
        return tuple(
            GroupFileDownloadReceipt(target.id, e.name, str(directory / e.name), *value)
            for e, value in zip(entries, values)
        )

    def download_folder(self, key, name, download_root, *, expected=None):
        """Use QQ's current download directory; does not change that setting.

        download_root must be the caller's known QQ download directory. Only a
        completely loaded flat folder is accepted, and no existing output is used.
        """
        name = file_name(name)
        root = Path(download_root).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("download_root must be an existing directory")
        directory = root / name
        if directory.exists() or directory.is_symlink():
            raise FileExistsError(directory)
        submitted = [False]
        with mutation_guard(submitted), self._fresh_view(key) as (target, window):
            before = unique_file_entry(
                self._entries(window), name, kind="folder", expected=expected
            )
            with self._folder(window, target, name):
                contents = self._entries(window)
                if (
                    not contents
                    or len(contents) != before.count
                    or any(e.kind != "file" for e in contents)
                ):
                    raise ControlUnavailable(
                        "Only fully loaded, nonempty flat folders are supported"
                    )
                if len({e.name.casefold() for e in contents}) != len(contents):
                    raise ControlUnavailable("Folder file names are ambiguous")
                for entry in contents:
                    file_name(entry.name)
            with self._folder_menu(window, name, expected=before) as (menu, current):
                if (
                    current.count != before.count
                    or directory.exists()
                    or directory.is_symlink()
                ):
                    raise TargetMismatch("Folder or download destination changed")
                action = self.ui._menu_item(menu, "下载")
                submitted[0] = True
                self.b.invoke(action)
                return self._download_receipts(target, contents, directory)

    @contextmanager
    def _file_menu(self, window, name, *, expected=None):
        rows, entries = self._read_rows(window)
        before = unique_file_entry(entries, name, kind="file", expected=expected)
        row = rows[entries.index(before)]

        def resolve_hover():
            nonlocal row
            current_rows = self._rows(window)
            current_entries = [
                parse_file_entry(self.b.snapshot(r)) for r in current_rows
            ]
            current = unique_file_entry(
                current_entries, name, kind="file", expected=before
            )
            row = current_rows[current_entries.index(current)]
            targets = [
                c
                for c in self.b.walk(row)
                if "file-container-left" in c.ClassName.split()
            ]
            if len(targets) != 1:
                raise ControlUnavailable("File hover target is missing or ambiguous")
            return targets[0]

        # The left child contains the icon and its sibling transfer-status overlay.
        # Keep the real hit check strict while accepting either observed subtree.
        with hover_control(self.b, None, resolve=resolve_hover):

            def checkbox():
                items = [
                    c
                    for c in self.b.walk(row)
                    if c.ControlTypeName == "CheckBoxControl" and not c.IsOffscreen
                ]
                if len(items) > 1:
                    raise ControlUnavailable("File row selection is ambiguous")
                return items[0] if items else None

            control = wait_until(checkbox)
            if not control.IsKeyboardFocusable:
                raise ControlUnavailable(
                    "File checkbox cannot be focused without toggling"
                )
            menu = self.ui._context(control)
            try:
                yield menu, before
            finally:
                self.ui._dismiss_menu()

    def _named_dialog(self, window, title, body):
        dialog = wait_until(lambda: self._dialog(window))
        node = self.b.snapshot(dialog)
        titles = [
            text_of(n)
            for n in node.walk()
            if n.has_class("q-dialog-window-title") or n.has_class("q-dialog-title")
        ]
        labels = [n.name for n in node.walk() if n.type == "TextControl"]
        if titles != [title] or body not in labels:
            raise ControlUnavailable("Unrecognized group-file confirmation dialog")
        return dialog

    def _name_input(self, dialog):
        edits = [
            c
            for c in self.b.walk(dialog)
            if c.ControlTypeName == "EditControl"
            and "q-input__inner" in c.ClassName.split()
        ]
        if len(edits) != 1 or edits[0].GetValuePattern() is None:
            raise ControlUnavailable("File name input is missing or ambiguous")
        return edits[0].GetValuePattern()

    def move_file(self, key, name, folder, *, source_folder=None, expected=None):
        name = file_name(name)
        folder = file_name(folder) if folder is not None else None
        source_folder = file_name(source_folder) if source_folder is not None else None
        if source_folder == folder:
            raise ValueError("Source and destination folder must differ")
        submitted = [False]
        with (
            ui_transaction(),
            mutation_guard(submitted),
            self.secondary.files_view(key) as (target, window),
        ):
            self._require_clean(window, target)
            root_entries = self._entries(window)
            for requested in (source_folder, folder):
                if requested is not None:
                    unique_file_entry(root_entries, requested, kind="folder")
            destination_view = (
                self._folder(window, target, folder) if folder else nullcontext()
            )
            with destination_view:
                self._require_absent(window, name)
            source_view = (
                self._folder(window, target, source_folder)
                if source_folder
                else nullcontext()
            )
            with (
                source_view,
                self._file_menu(window, name, expected=expected) as (menu, before),
            ):
                leaf = self.ui._menu_item(menu, "移动")
                if not leaf.IsEnabled:
                    raise ControlUnavailable("Move file is disabled")
                self.b.invoke(leaf)
                dialog = self._named_dialog(window, "移动文件", "移动到")
                label = folder or "群文件"
                choices = [
                    c
                    for c in self.b.walk(dialog)
                    if "item" in c.ClassName.split()
                    and any(
                        x.ControlTypeName == "TextControl" and x.Name == label
                        for x in c.GetChildren()
                    )
                ]
                if len(choices) != 1 or not choices[0].IsEnabled:
                    raise ControlUnavailable("Move destination is missing or ambiguous")
                if ("parent" in choices[0].ClassName.split()) != (folder is None):
                    raise TargetMismatch("Move destination kind differs")
                try:
                    self.b.invoke(choices[0])
                    wait_until(lambda: "selected" in choices[0].ClassName.split())
                    selected = [
                        c
                        for c in self.b.walk(dialog)
                        if "item" in c.ClassName.split()
                        and "selected" in c.ClassName.split()
                    ]
                    if (
                        len(selected) != 1
                        or text_of(self.b.snapshot(selected[0])) != label
                    ):
                        raise TargetMismatch("Selected move destination differs")
                    confirm = self._button(dialog, "确定")
                    submitted[0] = True
                    self.b.invoke(confirm)
                    wait_until(lambda: self._dialog(window), lambda d: d is None)
                    wait_until(
                        lambda: any(e.name == name for e in self._entries(window)),
                        lambda found: not found,
                    )
                finally:
                    if not submitted[0] and self._dialog(window) is not None:
                        self.b.invoke(self._button(dialog, "取消"))
            destination_view = (
                self._folder(window, target, folder) if folder else nullcontext()
            )
            with destination_view:
                after = wait_until(
                    lambda: next(
                        (e for e in self._entries(window) if e.name == name), None
                    )
                )
                unique_file_entry(
                    self._entries(window), name, kind="file", expected=before
                )
            return GroupFileMutationReceipt(
                target.id,
                "move_file",
                name,
                before,
                after,
                scope="loaded_folder" if folder else "loaded_root",
                source_folder=source_folder,
                folder=folder,
            )

    def rename_file(self, key, name, new_name, *, expected=None):
        name, new_name = file_name(name), file_name(new_name)
        if name.casefold() == new_name.casefold():
            raise ValueError("The new file name must differ beyond letter case")
        if Path(name).suffix != Path(new_name).suffix:
            raise ValueError("QQ's observed rename dialog preserves the file extension")
        submitted = [False]
        with (
            ui_transaction(),
            mutation_guard(submitted),
            self.secondary.files_view(key) as (target, window),
        ):
            self._require_clean(window, target)
            self._require_absent(window, new_name)
            with self._file_menu(window, name, expected=expected) as (menu, before):
                leaf = self.ui._menu_item(menu, "重命名")
                if not leaf.IsEnabled:
                    raise ControlUnavailable("Rename file is disabled")
                self.b.invoke(leaf)
                dialog = wait_until(lambda: self._dialog(window))
                try:
                    self._named_dialog(window, "重命名", "请输入文件名称")
                    value = self._name_input(dialog)
                    if value.Value != Path(name).stem:
                        raise TargetMismatch(
                            "Rename dialog does not contain the selected filename"
                        )
                    value.SetValue(Path(new_name).stem, waitTime=0.05)
                    if value.Value != Path(new_name).stem:
                        raise TargetMismatch("New filename verification failed")
                    confirm = self._button(dialog, "确定")
                    submitted[0] = True
                    self.b.invoke(confirm)
                    wait_until(lambda: self._dialog(window), lambda d: d is None)
                    after = self._await_entry(window, new_name, "file")
                    wait_until(
                        lambda: any(
                            entry.name == name for entry in self._entries(window)
                        ),
                        lambda present: not present,
                    )
                    if (
                        before.size_text != after.size_text
                        or before.uploader_text != after.uploader_text
                    ):
                        raise TargetMismatch(
                            "Renamed file metadata differs from the selected file"
                        )
                    return GroupFileMutationReceipt(
                        target.id, "rename_file", new_name, before, after
                    )
                finally:
                    if not submitted[0] and self._dialog(window) is not None:
                        self.b.invoke(self._button(dialog, "取消"))

    @contextmanager
    def _folder_menu(self, window, name, *, expected=None):
        from quitbot_backend._engine.native import show_context_menu

        rows, entries = self._read_rows(window)
        before = unique_file_entry(entries, name, kind="folder", expected=expected)
        if self.b.find(window, lambda c: c.ControlTypeName == "MenuControl", False):
            raise ControlUnavailable("Existing file menu retained")
        row = rows[entries.index(before)]
        # Folders expose no focusable checkbox. Native UIA avoids invoking/opening
        # the folder merely to acquire focus; the following dialog rechecks its name.
        show_context_menu(row)
        try:
            menu = wait_until(
                lambda: self.b.find(
                    window, lambda c: c.ControlTypeName == "MenuControl", False
                )
            )
            yield menu, before
        finally:
            self.ui._dismiss_menu()

    def rename_folder(self, key, name, new_name, *, expected=None):
        name, new_name = file_name(name), file_name(new_name)
        if name.casefold() == new_name.casefold():
            raise ValueError("Folder names must differ beyond letter case")
        submitted = [False]
        with (
            ui_transaction(),
            mutation_guard(submitted),
            self.secondary.files_view(key) as (target, window),
        ):
            self._require_clean(window, target)
            self._require_absent(window, new_name)
            with self._folder_menu(window, name, expected=expected) as (menu, before):
                leaf = self.ui._menu_item(menu, "重命名")
                if not leaf.IsEnabled:
                    raise ControlUnavailable("Rename folder is disabled")
                self.b.invoke(leaf)
                dialog = wait_until(lambda: self._dialog(window))
                try:
                    self._named_dialog(window, "重命名", "请输入文件夹名称")
                    value = self._name_input(dialog)
                    if value.Value != name:
                        raise TargetMismatch(
                            "Rename dialog identifies a different folder"
                        )
                    value.SetValue(new_name, waitTime=0.05)
                    if value.Value != new_name:
                        raise TargetMismatch("Folder name input differs")
                    submitted[0] = True
                    self.b.invoke(self._button(dialog, "确定"))
                    wait_until(
                        lambda: self._dialog(window), lambda current: current is None
                    )
                    after = self._await_entry(window, new_name, "folder")
                    wait_until(
                        lambda: any(e.name == name for e in self._entries(window)),
                        lambda present: not present,
                    )
                    if before.count != after.count:
                        raise TargetMismatch(
                            "Folder content count changed during rename"
                        )
                    return GroupFileMutationReceipt(
                        target.id, "rename_folder", new_name, before, after
                    )
                finally:
                    if not submitted[0] and self._dialog(window) is not None:
                        self.b.invoke(self._button(dialog, "取消"))

    def delete_folder(self, key, name, *, expected=None):
        """Delete an empty, uniquely loaded root folder; never infer recursive consent."""
        name = file_name(name)
        submitted = [False]
        with (
            ui_transaction(),
            mutation_guard(submitted),
            self.secondary.files_view(key) as (target, window),
        ):
            self._require_clean(window, target)
            with self._folder_menu(window, name, expected=expected) as (menu, before):
                if before.count != 0:
                    raise ControlUnavailable(
                        "Only an explicitly empty folder can be deleted"
                    )
                leaf = self.ui._menu_item(menu, "删除")
                if not leaf.IsEnabled:
                    raise ControlUnavailable("Delete folder is disabled")
                self.b.invoke(leaf)
                dialog = wait_until(lambda: self._dialog(window))
                try:
                    self._named_dialog(
                        window,
                        "删除文件夹",
                        f"删除后不可恢复，确定删除文件夹“{name}”吗？",
                    )
                    current = unique_file_entry(
                        self._entries(window), name, kind="folder", expected=before
                    )
                    if current.count != 0:
                        raise TargetMismatch("Folder acquired content before deletion")
                    submitted[0] = True
                    self.b.invoke(self._button(dialog, "确定"))
                    wait_until(
                        lambda: self._dialog(window), lambda current: current is None
                    )
                    wait_until(
                        lambda: any(e.name == name for e in self._entries(window)),
                        lambda present: not present,
                    )
                    return GroupFileMutationReceipt(
                        target.id, "delete_folder", name, before, None
                    )
                finally:
                    if not submitted[0] and self._dialog(window) is not None:
                        self.b.invoke(self._button(dialog, "取消"))

    def delete_files(self, key, names, *, expected=None):
        """Delete an exact selection of root files with one native confirmation."""
        if isinstance(names, str):
            raise TypeError("names must be a sequence")
        names = tuple(file_name(name) for name in names)
        if not 2 <= len(names) <= 20 or len({name.casefold() for name in names}) != len(names):
            raise ValueError("Provide 2..20 distinct file names")
        expected = expected or {}
        submitted = [False]
        with mutation_guard(submitted), self._fresh_view(key) as (target, window):
            from quitbot_backend._engine.native import show_context_menu

            rows, entries = self._read_rows(window)
            chosen = [unique_file_entry(entries, name, kind="file", expected=expected.get(name))
                      for name in names]

            def verify_selection():
                rows, entries = self._read_rows(window)
                selected = [entry.name for row, entry in zip(rows, entries)
                            if any(c.ControlTypeName == "CheckBoxControl"
                                   and c.GetTogglePattern().ToggleState == 1
                                   for c in self.b.walk(row))]
                if len(selected) != len(names) or set(selected) != set(names):
                    raise TargetMismatch("Batch file selection changed before deletion")
                for entry in chosen:
                    unique_file_entry(entries, entry.name, kind="file", expected=entry)

            try:
                show_context_menu(rows[entries.index(chosen[0])])
                self.b.invoke(self.ui._menu_item(wait_until(self.ui.navigation._menu), "多选"))
                wait_until(lambda: self.ui.by_class("forward-ops", window))
                for entry in chosen[1:]:
                    rows, entries = self._read_rows(window)
                    current = unique_file_entry(entries, entry.name, kind="file", expected=entry)
                    checkbox = self.b.find(rows[entries.index(current)],
                                           lambda c: c.ControlTypeName == "CheckBoxControl")
                    if checkbox.GetTogglePattern().ToggleState != 0:
                        raise TargetMismatch("Unexpected preselected file")
                    checkbox.GetTogglePattern().Toggle(waitTime=0.1)
                    wait_until(lambda checkbox=checkbox: checkbox.GetTogglePattern().ToggleState,
                               lambda value: value == 1)
                verify_selection()
                toolbar = self.ui.by_class("forward-ops", window, required=True)
                action = self.b.find(toolbar, lambda c: c.ControlTypeName == "ListItemControl"
                                     and (c.Name or text_of(self.b.snapshot(c))) == "删除")
                icon = self.ui.by_class("op-icon", action, required=True)
                if not icon.IsEnabled or icon.IsOffscreen or "disabled" in icon.ClassName.split():
                    raise ControlUnavailable("Batch file deletion is disabled")
                self.b.invoke(icon)
                dialog = wait_until(lambda: self._dialog(window))
                try:
                    self._named_dialog(window, "批量删除", f"确定删除已选中的{len(names)}个文件吗？")
                    verify_selection()
                    confirm = self._button(dialog, "确定")
                    submitted[0] = True
                    self.b.invoke(confirm)
                    wait_until(lambda: self._dialog(window), lambda current: current is None)
                    wait_until(lambda: self._entries(window),
                               lambda current: not any(entry.name in names for entry in current))
                    return tuple(GroupFileMutationReceipt(target.id, "delete_files", entry.name,
                                                          entry, None) for entry in chosen)
                finally:
                    if not submitted[0] and self._dialog(window) is not None:
                        self.b.invoke(self._button(dialog, "取消"))
            finally:
                self.ui._dismiss_menu()
                if self.ui.by_class("forward-ops", window) is not None:
                    self.b.invoke(self.ui.by_class("close-btn", window, required=True))
                    wait_until(lambda: self.ui.by_class("forward-ops", window), lambda value: value is None)

    def delete_file(self, key, name, *, expected=None):
        name = file_name(name)
        submitted = [False]
        with (
            ui_transaction(),
            mutation_guard(submitted),
            self.secondary.files_view(key) as (target, window),
        ):
            self._require_clean(window, target)
            with self._file_menu(window, name, expected=expected) as (menu, before):
                leaf = self.ui._menu_item(menu, "删除")
                if not leaf.IsEnabled:
                    raise ControlUnavailable("Delete file is disabled")
                self.b.invoke(leaf)
                dialog = wait_until(lambda: self._dialog(window))
                try:
                    self._named_dialog(window, "提示", f"你确定要删除文件{name}吗？")
                    confirm = self._button(dialog, "确定")
                    submitted[0] = True
                    self.b.invoke(confirm)
                    wait_until(lambda: self._dialog(window), lambda d: d is None)
                    wait_until(
                        lambda: any(
                            entry.name == name for entry in self._entries(window)
                        ),
                        lambda present: not present,
                    )
                    return GroupFileMutationReceipt(
                        target.id, "delete_file", name, before, None
                    )
                finally:
                    if not submitted[0] and self._dialog(window) is not None:
                        self.b.invoke(self._button(dialog, "取消"))

    def download_file(self, key, name, destination, *, expected=None):
        name = file_name(name)
        requested = Path(destination).expanduser()
        file_name(requested.name)
        if requested.exists() or requested.is_symlink():
            raise FileExistsError("Download destination must not already exist")
        path = requested.parent.resolve(strict=True) / requested.name
        if not path.parent.is_dir():
            raise ValueError("Download parent must be an existing directory")
        if Path(name).suffix != path.suffix:
            raise ValueError("Preserve the filename extension for native Save As")
        submitted = [False]
        with (
            ui_transaction(),
            mutation_guard(submitted),
            self.secondary.files_view(key) as (target, window),
        ):
            self._require_clean(window, target)
            with self._file_menu(window, name, expected=expected) as (menu, before):
                leaf = self.ui._menu_item(menu, "另存为")
                if not leaf.IsEnabled:
                    raise ControlUnavailable("Save As is disabled")
                self.b.invoke(leaf)
                picker = wait_until(lambda: self._picker(window))
                try:
                    if hasattr(picker, "set_path"):
                        picker.set_path(path, expected_name=name)
                        if path.exists() or path.is_symlink():
                            raise FileExistsError("Download destination appeared before confirmation")
                        submitted[0] = True
                        picker.confirm()
                    else:
                        if picker.Name != "另存为":
                            raise ControlUnavailable(
                                "Unrecognized group-file Save As picker"
                            )
                        edits = [
                            c
                            for c in self.b.walk(picker)
                            if c.ControlTypeName == "EditControl"
                            and c.AutomationId == "1001"
                        ]
                        if len(edits) != 1 or edits[0].GetValuePattern() is None:
                            raise ControlUnavailable(
                                "Save As filename input is missing or ambiguous"
                            )
                        value = edits[0].GetValuePattern()
                        if value.Value != name:
                            raise TargetMismatch(
                                "Save As picker does not identify the selected file"
                            )
                        value.SetValue(str(path), waitTime=0.05)
                        if value.Value != str(path):
                            raise TargetMismatch("Save As destination verification failed")
                        if path.exists() or path.is_symlink():
                            raise FileExistsError(
                                "Download destination appeared before confirmation"
                            )
                        confirm = self.b.find(
                            picker,
                            lambda c: (
                                c.ControlTypeName == "ButtonControl"
                                and c.AutomationId == "1"
                                and c.Name == "另存为"
                                and c.ClassName == "Button"
                            ),
                        )
                        submitted[0] = True
                        self.b.invoke(confirm)
                    wait_until(lambda: self._picker(window), lambda d: d is None)
                    size, digest = wait_until(
                        lambda: downloaded_file_state(path, before.size_text),
                        timeout=45,
                    )
                    return GroupFileDownloadReceipt(
                        target.id, name, str(path), size, digest
                    )
                finally:
                    if not submitted[0] and self._picker(window) is not None:
                        self._cancel_picker(picker)

    def _await_entry(self, window, name, kind, *, timeout=15):
        def read():
            entries = self._entries(window)
            if not any(entry.name == name for entry in entries):
                return None
            return unique_file_entry(entries, name, kind=kind)

        return wait_until(read, timeout=timeout)

    def create_folder(self, key, name):
        name = file_name(name)
        submitted = [False]
        with (
            ui_transaction(),
            mutation_guard(submitted),
            self.secondary.files_view(key) as (target, window),
        ):
            self._require_clean(window, target)
            self._require_absent(window, name)
            self.b.invoke(self._button(window, "新建文件夹"))
            dialog = wait_until(lambda: self._dialog(window))
            try:
                self._named_dialog(window, "新建文件夹", "请输入文件夹名称")
                value = self._name_input(dialog)
                value.SetValue(name, waitTime=0.05)
                if value.Value != name:
                    raise TargetMismatch(
                        "Folder name did not retain the requested value"
                    )
                confirm = self._button(dialog, "确定")
                submitted[0] = True
                self.b.invoke(confirm)
                wait_until(lambda: self._dialog(window), lambda d: d is None)
                after = self._await_entry(window, name, "folder")
                return GroupFileMutationReceipt(
                    target.id, "create_folder", name, None, after
                )
            finally:
                if not submitted[0] and self._dialog(window) is not None:
                    self.b.invoke(self._button(dialog, "取消"))

    def upload_file(self, key, path):
        path = Path(path).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError("Upload requires an existing regular file")
        name = file_name(path.name)
        submitted = [False]
        with (
            ui_transaction(),
            mutation_guard(submitted),
            self.secondary.files_view(key) as (target, window),
        ):
            self._require_clean(window, target)
            self._require_absent(window, name)
            self.b.invoke(self._button(window, "上传文件"))
            picker = wait_until(lambda: self._picker(window))
            try:
                if hasattr(picker, "set_path"):
                    picker.set_path(path)
                    submitted[0] = True
                    picker.confirm()
                else:
                    if picker.Name != "请选择":
                        raise ControlUnavailable("Unrecognized group upload picker")
                    edits = [
                        c
                        for c in self.b.walk(picker)
                        if c.ControlTypeName == "EditControl" and c.AutomationId == "1148"
                    ]
                    if len(edits) != 1 or edits[0].GetValuePattern() is None:
                        raise ControlUnavailable(
                            "Native filename input is missing or ambiguous"
                        )
                    value = edits[0].GetValuePattern()
                    value.SetValue(str(path), waitTime=0.05)
                    if value.Value != str(path):
                        raise TargetMismatch("Upload picker path verification failed")
                    confirm = self.b.find(
                        picker,
                        lambda c: (
                            c.ControlTypeName == "ButtonControl"
                            and c.AutomationId == "1"
                            and c.ClassName == "Button"
                            and c.Name == "确认"
                        ),
                    )
                    # The group-file picker commits immediately; there is no chat
                    # send preview and retrying would risk a duplicate upload.
                    submitted[0] = True
                    self.b.invoke(confirm)
                wait_until(lambda: self._picker(window), lambda d: d is None)
                after = self._await_entry(window, name, "file", timeout=45)
                return GroupFileMutationReceipt(
                    target.id,
                    "upload_file",
                    name,
                    None,
                    after,
                    local_path=str(path),
                )
            finally:
                if not submitted[0] and self._picker(window) is not None:
                    self._cancel_picker(picker)
