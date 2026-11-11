"""Favorite sticker operations from the observed native collection panel."""

from contextlib import contextmanager
from pathlib import Path

from ...domain.stickers import FavoriteSticker
from ...errors import ControlUnavailable, DraftNotEmpty, TargetMismatch
from ...locking import ui_transaction
from ...models import editor_has_content
from ...uia import wait_until
from .group_files import GroupFilesUI
from .moderation import mutation_guard


def select_sticker(items, selector, account, hwnd):
    if isinstance(selector, FavoriteSticker):
        if (selector.account, selector.hwnd) != (account, hwnd):
            raise TargetMismatch("Sticker reference belongs to another QQ instance")
        matches = [
            i
            for i in items
            if i.runtime_id == selector.runtime_id and i.name == selector.name
        ]
    elif isinstance(selector, str) and selector and selector != "自定义表情":
        matches = [i for i in items if i.name == selector]
    else:
        raise ValueError(
            "Use a current FavoriteSticker reference or unique nondefault remark"
        )
    if len(matches) != 1:
        raise ControlUnavailable(
            "Sticker is stale, missing or ambiguous; read the collection again"
        )
    return matches[0]


class StickersUI:
    def __init__(self, client):
        self.client, self.b, self.ui = client, client.backend, client.control_ui

    def _rows(self):
        return [
            c
            for c in self.b.walk(self.ui.by_class("sticker-panel", required=True))
            if c.ClassName == "collection-sticker"
        ]

    def _items(self):
        return tuple(
            FavoriteSticker(
                self.b.account, self.b.hwnd, tuple(c.GetRuntimeId()), c.Name, i
            )
            for i, c in enumerate(self._rows())
        )

    def _select(self, selector):
        item = select_sticker(self._items(), selector, self.b.account, self.b.hwnd)
        rows = [
            c
            for c in self._rows()
            if tuple(c.GetRuntimeId()) == item.runtime_id and c.Name == item.name
        ]
        if len(rows) != 1:
            raise ControlUnavailable("Sticker identity changed before interaction")
        return rows[0], item

    def _button(self, root, name):
        return self.b.find(
            root, lambda c: c.Name == name and c.ControlTypeName == "ButtonControl"
        )

    @contextmanager
    def panel(self, key):
        b, ui = self.b, self.ui
        active = getattr(self, "_active_panel", None)
        if active is not None:
            if (
                self.client.target(key) != self._active_target
                or b.header().Name != self._active_target.name
            ):
                raise TargetMismatch("Active sticker collection conversation changed")
            yield active
            return
        with ui.checked(key):
            if (
                ui.by_class("sticker-panel") is not None
                or ui.by_class("q-dialog-main") is not None
            ):
                raise ControlUnavailable("Existing panel/dialog retained")
            if editor_has_content(b.snapshot(b.editor())):
                raise DraftNotEmpty("Existing composer draft retained")
            b.invoke(self._button(b.window, "表情"))
            panel = wait_until(lambda: ui.by_class("sticker-panel"))
            original = None
            try:
                tabs = [
                    c
                    for c in b.walk(panel)
                    if "tabs-container-item" in c.ClassName.split()
                ]
                selected = [
                    c.Name for c in tabs if c.GetTogglePattern().ToggleState == 1
                ]
                if len(selected) != 1:
                    raise ControlUnavailable("Sticker category is ambiguous")
                original = selected[0]
                favorite = next(c for c in tabs if c.Name == "我的收藏")
                if favorite.GetTogglePattern().ToggleState != 1:
                    favorite.GetTogglePattern().Toggle(waitTime=0.2)
                self._active_panel = panel
                self._active_target = self.client.target(key)
                yield panel
            finally:
                self._active_panel = None
                self._active_target = None
                ui._dismiss_menu()
                dialog = ui.by_class("modify-emoji-desc-dialog-main")
                if dialog is not None:
                    b.invoke(self._button(dialog, "取消"))
                picker = GroupFilesUI(self.client)._picker(b.window)
                if picker is not None:
                    b.invoke(
                        b.find(
                            picker,
                            lambda c: (
                                c.AutomationId == "2"
                                and c.ControlTypeName == "ButtonControl"
                            ),
                        )
                    )
                current = ui.by_class("sticker-panel")
                if current is not None:
                    tab = b.find(
                        current,
                        lambda c: (
                            c.Name == original
                            and "tabs-container-item" in c.ClassName.split()
                        ),
                        False,
                    )
                    if tab is not None and tab.GetTogglePattern().ToggleState != 1:
                        tab.GetTogglePattern().Toggle(waitTime=0.1)
                    trigger = self._button(b.window, "表情")
                    b.focus_control(trigger)
                    b.ensure_focus(trigger)
                    b.auto.SendKeys("{Esc}", waitTime=0.1)
                    wait_until(
                        lambda: ui.by_class("sticker-panel"), lambda c: c is None
                    )

    def list(self, key):
        with self.panel(key):
            return self._items()

    def _menu_action(self, row, name):
        from comtypes.gen.UIAutomationClient import IUIAutomationElement3

        row.Element.QueryInterface(IUIAutomationElement3).ShowContextMenu()
        menu = wait_until(self.ui.navigation._menu)
        return self.ui._menu_item(menu, name)

    def import_images(self, key, paths):
        if isinstance(paths, (str, Path)):
            raise TypeError("paths must be a sequence of PNG or GIF paths")
        paths = tuple(Path(p).expanduser().resolve(strict=True) for p in paths)
        if not 1 <= len(paths) <= 20 or len(set(paths)) != len(paths):
            raise ValueError("Provide 1..20 distinct PNG or GIF files")
        for path in paths:
            if not path.is_file():
                raise ValueError("Import requires local PNG or GIF files")
            with path.open("rb") as source:
                header = source.read(8)
            if not (
                (path.suffix.lower() == ".png" and header == b"\x89PNG\r\n\x1a\n")
                or (path.suffix.lower() == ".gif" and header[:6] in {b"GIF87a", b"GIF89a"})
            ):
                raise ValueError("Only local PNG and GIF imports have been verified")
        submitted = [False]
        with ui_transaction(), mutation_guard(submitted), self.panel(key) as panel:
            before = self._items()
            self.b.invoke(
                self.b.find(
                    panel,
                    lambda c: (
                        c.Name == "导入表情" and c.ClassName == "sticker-list__import"
                    ),
                )
            )
            picker = wait_until(
                lambda: GroupFilesUI(self.client)._picker(self.b.window)
            )
            field = self.b.find(
                picker,
                lambda c: (
                    c.AutomationId == "1148" and c.ControlTypeName == "EditControl"
                ),
            )
            value = " ".join('"' + str(p) + '"' for p in paths)
            field.GetValuePattern().SetValue(value, waitTime=0.1)
            if field.GetValuePattern().Value != value:
                raise TargetMismatch("Import path field differs")
            submitted[0] = True
            self.b.invoke(
                self.b.find(
                    picker,
                    lambda c: (
                        c.AutomationId == "1"
                        and c.Name == "确认"
                        and c.ControlTypeName == "ButtonControl"
                    ),
                )
            )
            wait_until(
                lambda: GroupFilesUI(self.client)._picker(self.b.window),
                lambda c: c is None,
            )
            after = wait_until(
                self._items, lambda xs: len(xs) == len(before) + len(paths)
            )
            old_ids = {i.runtime_id for i in before}
            if not old_ids.issubset({i.runtime_id for i in after}):
                raise ControlUnavailable(
                    "Existing collection identities changed during import"
                )
            # QQ's order differs from file-selection order; never zip refs to paths.
            return tuple(i for i in after if i.runtime_id not in old_ids)

    def move_first(self, key, selector):
        submitted = [False]
        with ui_transaction(), mutation_guard(submitted), self.panel(key):
            row, item = self._select(selector)
            if item.index == 0:
                return item
            action = self._menu_action(row, "移至最前")
            submitted[0] = True
            self.b.invoke(action)
            return wait_until(
                self._items, lambda xs: bool(xs) and xs[0].runtime_id == item.runtime_id
            )[0]

    def delete(self, key, selector):
        submitted = [False]
        with ui_transaction(), mutation_guard(submitted), self.panel(key):
            before = self._items()
            row, item = self._select(selector)
            action = self._menu_action(row, "删除")
            submitted[0] = True
            self.b.invoke(action)
            after = wait_until(self._items, lambda xs: len(xs) == len(before) - 1)
            expected = {i.runtime_id for i in before} - {item.runtime_id}
            if {i.runtime_id for i in after} != expected:
                raise ControlUnavailable(
                    "Collection change does not match exact deletion"
                )
            return item

    def remark(self, key, selector, value=None):
        if value is not None and (
            not isinstance(value, str)
            or not 1 <= len(value) <= 15
            or any(ord(c) < 32 for c in value)
            or value == "自定义表情"
        ):
            raise ValueError("Use a nondefault 1..15-character remark without controls")
        submitted = [False]
        with ui_transaction(), mutation_guard(submitted):
            with self.panel(key):
                row, item = self._select(selector)
                if value is not None and any(
                    i.name == value and i.runtime_id != item.runtime_id
                    for i in self._items()
                ):
                    raise ControlUnavailable("New remark would not be unique")
                self.b.invoke(self._menu_action(row, "修改备注"))
                dialog = wait_until(
                    lambda: self.ui.by_class("modify-emoji-desc-dialog-main")
                )
                field = self.b.find(
                    dialog,
                    lambda c: (
                        c.Name == "请输入备注" and c.ControlTypeName == "EditControl"
                    ),
                )
                before = field.GetValuePattern().Value
                if value is None or value == before:
                    return before
                field.GetValuePattern().SetValue(value, waitTime=0.1)
                wait_until(lambda: field.GetValuePattern().Value, lambda v: v == value)
                self.b.focus_control(field)
                self.b.ensure_focus(field)
                self.b.auto.SendKeys("{Tab}", waitTime=0.1)
                if field.GetValuePattern().Value != value:
                    raise ControlUnavailable(
                        "QQ normalized the remark before submission; cancelled"
                    )
                confirm = self._button(dialog, "确定")
                if not confirm.IsEnabled:
                    raise ControlUnavailable("Remark confirmation is disabled")
                submitted[0] = True
                self.b.invoke(confirm)
                wait_until(
                    lambda: self.ui.by_class("modify-emoji-desc-dialog-main"),
                    lambda c: c is None,
                )
            # Reopen by its unique native name; no cross-panel index assumption.
            with self.panel(key):
                # A caller-owned collection scope keeps the same panel alive.
                # Its runtime key can verify the editor before Name is refreshed.
                current = [i for i in self._items() if i.runtime_id == item.runtime_id]
                row, _ = self._select(current[0] if len(current) == 1 else value)
                self.b.invoke(self._menu_action(row, "修改备注"))
                dialog = wait_until(
                    lambda: self.ui.by_class("modify-emoji-desc-dialog-main")
                )
                field = self.b.find(
                    dialog,
                    lambda c: (
                        c.Name == "请输入备注" and c.ControlTypeName == "EditControl"
                    ),
                )
                if field.GetValuePattern().Value != value:
                    raise TargetMismatch("Saved remark differs on reopen")
            return value
