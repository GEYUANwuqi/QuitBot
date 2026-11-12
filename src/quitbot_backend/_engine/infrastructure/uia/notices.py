"""Read the native announcement window from the verified main conversation."""

from contextlib import contextmanager
from pathlib import Path

from quitbot_backend._engine.domain.notices import (
    GroupNotice,
    GroupNoticeMutationReceipt,
    GroupNoticePinReceipt,
    GroupNotices,
)
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    DraftNotEmpty,
    MutationOutcomeUnknown,
    TargetMismatch,
)
from quitbot_backend._engine.locking import ui_transaction
from quitbot_backend._engine.models import text_of
from quitbot_backend._engine.uia import wait_until

from .inspection import hover_control, require_unlocked
from .moderation import mutation_guard


def matching_windows(backend, pid, title):
    # Collapse multiple provider representations of the same native HWND.
    matches = {}
    for window in backend.auto.GetRootControl().GetChildren():
        if (
            window.ProcessId == pid
            and window.Name == title
            and window.ControlTypeName == "WindowControl"
            and window.NativeWindowHandle
        ):
            matches[window.NativeWindowHandle] = window
    return list(matches.values())


def parse_notices(node, group_id, group_name):
    titles = [text_of(n) for n in node.walk() if n.has_class("notice-title")]
    lists = [n for n in node.walk() if n.has_class("notice-list")]
    if titles != [group_name] or len(lists) != 1:
        raise TargetMismatch("Announcement list does not identify the expected group")
    items = []
    for row in lists[0].walk():
        if not row.has_class("notice-item"):
            continue
        bodies = [text_of(n) for n in row.walk() if n.has_class("content__text")]
        info = [n for n in row.walk() if n.has_class("info")]
        if len(bodies) != 1 or len(info) != 1:
            raise ControlUnavailable("Unrecognized announcement row")
        labels = [n.name for n in info[0].walk() if n.type == "TextControl"]
        items.append(
            GroupNotice(
                bodies[0],
                *(labels if len(labels) == 2 else (None, None)),
                media_present=any(
                    n.has_class("content__media")
                    and any(child.type == "ImageControl" for child in n.walk())
                    for n in row.walk()
                ),
            )
        )
    return GroupNotices(str(group_id), tuple(items))


def unique_notice(items, text, expected=None):
    matches = [n for n in items if n.text == text]
    if len(matches) != 1:
        raise TargetMismatch(
            "Notice text must identify exactly one loaded announcement"
        )
    if expected is not None:
        if not isinstance(expected, GroupNotice):
            raise TypeError("expected must be the previously read GroupNotice")
        if matches[0] != expected:
            raise TargetMismatch("Notice metadata changed since selection")
    return matches[0]


def validate_notice_text(text):
    if (
        not isinstance(text, str)
        or not text.strip()
        or len(text) > 600
        or any(ord(c) < 32 and c != "\n" for c in text)
    ):
        raise ValueError("Notice text must contain 1..600 characters")


class NoticesUI:
    def __init__(self, client):
        self.client, self.b = client, client.backend

    def publish(self, key, text, *, image=None):
        validate_notice_text(text)
        source = (
            Path(image).expanduser().resolve(strict=True) if image is not None else None
        )
        if source is not None and (
            not source.is_file() or source.suffix.lower() != ".png"
        ):
            raise ValueError("Notice image currently requires one local PNG file")
        target = self.client.target(key)
        if target.kind != "group":
            raise ValueError("Announcements require a group")
        with ui_transaction():
            require_unlocked(self.b.auto)
            self.b.use_main_window()
            self.b.open_session(target.name)
            self.b.profile(target.id, target.name, "group")
            main, window = self.b.window, None

            def windows():
                return matching_windows(self.b, main.ProcessId, f"群公告-{target.name}")

            if windows():
                raise DraftNotEmpty("Existing announcement window retained")
            submitted, transitioned = False, False
            try:
                self.b.invoke(
                    self.b.find(
                        main,
                        lambda c: (
                            c.ControlTypeName == "ButtonControl"
                            and c.Name == "群公告"
                            and any(
                                token in c.ClassName.split()
                                for token in ("group-notice", "group-notice--edit")
                            )
                        ),
                    )
                )
                found = wait_until(windows, lambda values: len(values) == 1)
                if len(found) != 1:
                    raise ControlUnavailable("Announcement window is ambiguous")
                window = found[0]

                def content():
                    node = self.b.snapshot(window)
                    if any(n.has_class("notice-list") for n in node.walk()):
                        notices = parse_notices(node, target.id, target.name)
                        return ("list", notices) if notices.items else None
                    editor = self.b.find(
                        window,
                        lambda c: (
                            c.ControlTypeName == "EditControl"
                            and c.Name == "编辑器"
                            and "ck-content" in c.ClassName.split()
                        ),
                        False,
                    )
                    return ("editor", editor) if editor else None

                kind, loaded = wait_until(content)
                if kind == "list":
                    if any(n.text == text for n in loaded.items):
                        raise TargetMismatch(
                            "Identical visible notice already exists; refusing duplicate publication"
                        )
                    self.b.invoke(
                        self.b.find(
                            window,
                            lambda c: (
                                c.ControlTypeName == "ButtonControl"
                                and c.Name == "发布新公告"
                            ),
                        )
                    )
                editor = wait_until(
                    lambda: self.b.find(
                        window,
                        lambda c: (
                            c.ControlTypeName == "EditControl"
                            and c.Name == "编辑器"
                            and "ck-content" in c.ClassName.split()
                        ),
                        False,
                    )
                )
                if editor.GetValuePattern().Value.strip() != "填写公告，1-600字":
                    raise DraftNotEmpty("Existing announcement editor content retained")
                pinned = self.b.find(
                    window,
                    lambda c: (
                        c.ControlTypeName == "CheckBoxControl" and c.Name == "置顶公告"
                    ),
                )
                if pinned.GetTogglePattern().ToggleState != 0:
                    raise ControlUnavailable("Unexpected pinned notice default")
                self.b.window = window
                self.b.focus_control(editor)
                self.b.auto.SendKeys("{Ctrl}a", waitTime=0.05)
                self.b.type_literal(editor, text)
                wait_until(
                    lambda: editor.GetValuePattern().Value.rstrip("\n"),
                    lambda value: value == text,
                )
                if source is not None:
                    self._choose_image(window, source, fresh=True)
                    if editor.GetValuePattern().Value.rstrip("\n") != text:
                        raise TargetMismatch(
                            "Notice text changed while choosing an image"
                        )
                button = self.b.find(
                    window,
                    lambda c: c.ControlTypeName == "ButtonControl" and c.Name == "发布",
                )
                if not button.IsEnabled:
                    raise ControlUnavailable("Notice publication is disabled")
                submitted = True
                self.b.invoke(button)
                wait_until(
                    lambda: (
                        not windows()
                        or self.b.find(
                            window,
                            lambda c: (
                                c.ControlTypeName == "EditControl"
                                and c.Name == "编辑器"
                            ),
                            False,
                        )
                        is None
                    )
                )
                transitioned = True
            except Exception as error:
                if submitted:
                    raise MutationOutcomeUnknown(
                        "Announcement submission uncertain; inspect before retrying"
                    ) from error
                raise
            finally:
                self.b.window = main
                try:
                    if (
                        (not submitted or transitioned)
                        and window is not None
                        and any(
                            w.NativeWindowHandle == window.NativeWindowHandle
                            for w in windows()
                        )
                    ):
                        window.GetWindowPattern().Close(waitTime=0.1)
                except Exception as error:
                    if submitted:
                        raise MutationOutcomeUnknown(
                            "Announcement submitted but window cleanup failed; inspect before retrying"
                        ) from error
                    raise
            try:
                main.SetActive(waitTime=0.1)
                result = wait_until(
                    lambda: next(
                        (
                            n
                            for n in self.read(key).items
                            if n.text == text and (source is None or n.media_present)
                        ),
                        None,
                    ),
                    timeout=15,
                )
            except Exception as error:
                raise MutationOutcomeUnknown(
                    "Published announcement was not read back; inspect before retrying"
                ) from error
            return result

    @contextmanager
    def _managed_notice(self, key, text, expected):
        validate_notice_text(text)
        target = self.client.target(key)
        if target.kind != "group":
            raise ValueError("Announcements require a group")
        require_unlocked(self.b.auto)
        self.b.use_main_window()
        self.b.open_session(target.name)
        self.b.profile(target.id, target.name, "group")
        main, window = self.b.window, None

        def windows():
            return matching_windows(self.b, main.ProcessId, f"群公告-{target.name}")

        if windows():
            raise DraftNotEmpty("Existing announcement window retained")
        try:
            self.b.invoke(
                self.b.find(
                    main,
                    lambda c: (
                        c.ControlTypeName == "ButtonControl" and c.Name == "群公告"
                    ),
                )
            )
            found = wait_until(windows, lambda values: len(values) == 1)
            window = found[0]
            self.b.window = window

            def loaded():
                try:
                    value = parse_notices(
                        self.b.snapshot(window), target.id, target.name
                    )
                    return value if value.items else None
                except (ControlUnavailable, TargetMismatch):
                    return None

            notices = wait_until(loaded)
            before = unique_notice(notices.items, text, expected)
            yield target, window, notices, before
        finally:
            self.b.window = main
            main.SetActive(waitTime=0.1)
            if window is not None and any(
                w.NativeWindowHandle == window.NativeWindowHandle
                and w.ProcessId == main.ProcessId
                for w in self.b.auto.GetRootControl().GetChildren()
            ):
                window.GetWindowPattern().Close(waitTime=0.1)
            main.SetActive(waitTime=0.1)

    def _notice_action(self, target, window, before, action, submitted=None):
        from quitbot_backend._engine.native import show_context_menu

        ui = self.client.control_ui
        rows = [
            c
            for c in self.b.walk(window)
            if "notice-item" in c.ClassName.split()
            and (body := ui.by_class("content__text", c)) is not None
            and text_of(self.b.snapshot(body)) == before.text
        ]
        if len(rows) != 1:
            raise TargetMismatch("Announcement row changed before opening its menu")
        show_context_menu(rows[0])
        menu = ui.navigation._menu()
        unique_notice(
            parse_notices(self.b.snapshot(window), target.id, target.name).items,
            before.text,
            before,
        )
        leaf = ui._menu_item(menu, action)
        if not leaf.IsEnabled or leaf.IsOffscreen:
            raise ControlUnavailable(
                "Announcement operation is unavailable for this account"
            )
        if submitted is not None:
            submitted[0] = True
        self.b.invoke(leaf)

    def delete(self, key, text, *, expected=None):
        submitted = [False]
        with ui_transaction(), mutation_guard(submitted):
            with self._managed_notice(key, text, expected) as (
                target,
                window,
                notices,
                before,
            ):
                if len(notices.items) < 2:
                    raise ControlUnavailable(
                        "Deleting the sole visible notice requires an unverified empty-state reader"
                    )
                self._notice_action(target, window, before, "删除", submitted)
                wait_until(
                    lambda: parse_notices(
                        self.b.snapshot(window), target.id, target.name
                    ),
                    lambda value: (
                        value.items and not any(n.text == text for n in value.items)
                    ),
                )
            wait_until(
                lambda: self.read(key),
                lambda value: (
                    value.items and not any(n.text == text for n in value.items)
                ),
                timeout=15,
            )
            return GroupNoticeMutationReceipt(target.id, "delete_notice", before, None)

    def _editor_controls(self, window, before):
        editor = wait_until(
            lambda: self.b.find(
                window,
                lambda c: c.ControlTypeName == "EditControl" and c.Name == "编辑器",
                False,
            )
        )
        if editor.GetValuePattern().Value.rstrip("\n") != before.text:
            raise TargetMismatch(
                "Announcement editor does not contain the selected original text"
            )
        pin = self.b.find(
            window,
            lambda c: c.ControlTypeName == "CheckBoxControl" and c.Name == "置顶公告",
        )
        state = pin.GetTogglePattern().ToggleState
        if state not in (0, 1):
            raise ControlUnavailable("Pinned state is ambiguous")
        return editor, pin, bool(state)

    def _choose_image(self, window, source, *, fresh=False):
        from .group_files import GroupFilesUI

        ui = self.client.control_ui
        files = GroupFilesUI(self.client)
        self._check_editor_media(window, False)
        if fresh:
            trigger = ui.by_class("uploader__input", window, required=True)
            if trigger.Name != "选择文件: 未选择任何文件":
                raise DraftNotEmpty("Existing announcement image retained")
        else:
            trigger = ui.by_class("uploader__btn", window, required=True)
            if trigger.IsOffscreen or not trigger.IsEnabled:
                raise ControlUnavailable("Announcement image upload unavailable")
        self.b.invoke(trigger)
        picker = wait_until(lambda: files._picker(window))
        try:
            field = self.b.find(
                picker,
                lambda c: (
                    c.ControlTypeName == "EditControl" and c.AutomationId == "1148"
                ),
            )
            field.GetValuePattern().SetValue(str(source), waitTime=0.1)
            if field.GetValuePattern().Value != str(source):
                raise TargetMismatch("Notice image picker value changed")
            self.b.invoke(
                self.b.find(
                    picker,
                    lambda c: (
                        c.ControlTypeName == "ButtonControl" and c.AutomationId == "1"
                    ),
                )
            )
            wait_until(lambda: files._picker(window), lambda value: value is None)
        finally:
            current = files._picker(window)
            if current is not None:
                self.b.invoke(
                    self.b.find(
                        current,
                        lambda c: (
                            c.ControlTypeName == "ButtonControl"
                            and c.AutomationId == "2"
                        ),
                    )
                )

        def image_ready():
            viewer = ui.by_class("uploader-viewer", window)
            source_input = ui.by_class("uploader__input", window)
            return (
                viewer is not None
                and source_input is not None
                and source_input.Name == f"选择文件: {source.name}"
                and sum(
                    c.ControlTypeName == "ImageControl" for c in self.b.walk(viewer)
                )
                == 1
            )

        wait_until(image_ready)

    def _check_editor_media(self, window, present):
        viewer = self.client.control_ui.by_class("uploader-viewer", window)
        count = (
            sum(c.ControlTypeName == "ImageControl" for c in self.b.walk(viewer))
            if viewer is not None
            else 0
        )
        if (viewer is not None) != present or count != int(present):
            raise TargetMismatch("Announcement editor image state differs")

    def _remove_editor_image(self, window):
        ui = self.client.control_ui
        self._check_editor_media(window, True)
        rect = window.BoundingRectangle
        try:
            # This is our own temporary editor. Keep its thumbnail clear of
            # central dialogs; strict native hit testing still gates hovering.
            window.GetTransformPattern().Move(0, 0, waitTime=0.2)
            viewer = ui.by_class("uploader-viewer", window, required=True)
            with hover_control(self.b, viewer):
                remove = wait_until(lambda: ui.by_class("uploader-delete-btn", window))
                if remove.IsOffscreen or not remove.IsEnabled:
                    raise ControlUnavailable("Announcement image removal unavailable")
                self.b.invoke(remove)
                wait_until(
                    lambda: ui.by_class("uploader-viewer", window),
                    lambda value: value is None,
                )
        finally:
            window.GetTransformPattern().Move(rect.left, rect.top, waitTime=0.1)
        self._check_editor_media(window, False)

    def _submit_editor(self, window, submitted):
        button = self.b.find(
            window, lambda c: c.ControlTypeName == "ButtonControl" and c.Name == "发布"
        )
        if not button.IsEnabled or button.IsOffscreen:
            raise ControlUnavailable("Edited notice publication is unavailable")
        submitted[0] = True
        self.b.invoke(button)
        wait_until(
            lambda: (
                not any(
                    w.NativeWindowHandle == window.NativeWindowHandle
                    for w in self.b.auto.GetRootControl().GetChildren()
                )
                or self.b.find(
                    window,
                    lambda c: c.ControlTypeName == "EditControl" and c.Name == "编辑器",
                    False,
                )
                is None
            )
        )

    def pinned(self, key, text, *, expected=None):
        """Read the native editor checkbox; requires announcement editing permission."""
        with (
            ui_transaction(),
            self._managed_notice(key, text, expected) as (target, window, _, before),
        ):
            self._notice_action(target, window, before, "编辑")
            return self._editor_controls(window, before)[2]

    def set_pinned(self, key, text, enabled, *, expected=None):
        if type(enabled) is not bool:
            raise TypeError("enabled must be a boolean")
        submitted = [False]
        with ui_transaction(), mutation_guard(submitted):
            with self._managed_notice(key, text, expected) as (
                target,
                window,
                _,
                before,
            ):
                self._notice_action(target, window, before, "编辑")
                editor, pin, original = self._editor_controls(window, before)
                self._check_editor_media(window, before.media_present)
                if original == enabled:
                    return GroupNoticePinReceipt(target.id, before, original, original)
                pin.GetTogglePattern().Toggle(waitTime=0.1)
                wait_until(
                    lambda: pin.GetTogglePattern().ToggleState,
                    lambda value: value == int(enabled),
                )
                if editor.GetValuePattern().Value.rstrip("\n") != before.text:
                    raise TargetMismatch(
                        "Notice text changed while setting its pinned state"
                    )
                self._check_editor_media(window, before.media_present)
                self._submit_editor(window, submitted)
            with self._managed_notice(key, text, None) as (target, window, _, after):
                self._notice_action(target, window, after, "编辑")
                actual = self._editor_controls(window, after)[2]
                self._check_editor_media(window, before.media_present)
                if actual != enabled or after.media_present != before.media_present:
                    raise TargetMismatch(
                        "Pinned notice state was not preserved after reopening"
                    )
            return GroupNoticePinReceipt(target.id, after, original, actual)

    def edit(
        self, key, text, new_text, *, image=None, remove_image=False, expected=None
    ):
        validate_notice_text(new_text)
        if type(remove_image) is not bool:
            raise TypeError("remove_image must be a boolean")
        if image is not None and remove_image:
            raise ValueError("image and remove_image are mutually exclusive")
        source = (
            Path(image).expanduser().resolve(strict=True) if image is not None else None
        )
        if source is not None and (
            not source.is_file() or source.suffix.lower() != ".png"
        ):
            raise ValueError("Notice image currently requires one local PNG file")
        submitted = [False]
        with ui_transaction(), mutation_guard(submitted):
            with self._managed_notice(key, text, expected) as (
                target,
                window,
                notices,
                before,
            ):
                if source is not None and not before.media_present:
                    raise ControlUnavailable(
                        "Adding an image to an existing text announcement is not yet verified"
                    )
                if (
                    new_text == text
                    and source is None
                    and not (remove_image and before.media_present)
                ):
                    return GroupNoticeMutationReceipt(
                        target.id, "edit_notice", before, before
                    )
                if new_text != text and any(n.text == new_text for n in notices.items):
                    raise TargetMismatch("Replacement notice text is already present")
                self._notice_action(target, window, before, "编辑")
                editor, pin, pinned = self._editor_controls(window, before)
                self._check_editor_media(window, before.media_present)
                if new_text != text:
                    self.b.focus_control(editor)
                    self.b.auto.SendKeys("{Ctrl}a", waitTime=0.05)
                    self.b.type_literal(editor, new_text)
                    wait_until(
                        lambda: editor.GetValuePattern().Value.rstrip("\n"),
                        lambda value: value == new_text,
                    )
                if before.media_present and (source is not None or remove_image):
                    self._remove_editor_image(window)
                if source is not None:
                    self._choose_image(window, source)
                media_present = source is not None or (
                    before.media_present and not remove_image
                )
                if pin.GetTogglePattern().ToggleState != pinned:
                    raise TargetMismatch("Pinned state changed while editing the text")
                if editor.GetValuePattern().Value.rstrip("\n") != new_text:
                    raise TargetMismatch("Notice text changed while editing its image")
                self._check_editor_media(window, media_present)
                self._submit_editor(window, submitted)
            updated = wait_until(
                lambda: self.read(key),
                lambda value: (
                    any(n.text == new_text for n in value.items)
                    and (
                        new_text == text or not any(n.text == text for n in value.items)
                    )
                    and any(
                        n.text == new_text and n.media_present == media_present
                        for n in value.items
                    )
                ),
                timeout=15,
            )
            after = unique_notice(updated.items, new_text)
            with self._managed_notice(key, new_text, after) as (
                target,
                window,
                _,
                after,
            ):
                self._notice_action(target, window, after, "编辑")
                if self._editor_controls(window, after)[2] != pinned:
                    raise TargetMismatch(
                        "Edited announcement did not preserve its pinned state"
                    )
                self._check_editor_media(window, media_present)
            return GroupNoticeMutationReceipt(target.id, "edit_notice", before, after)

    def read(self, key):
        target = self.client.target(key)
        if target.kind != "group":
            raise ValueError("Announcements require a group")
        with ui_transaction():
            require_unlocked(self.b.auto)
            self.b.open_session(target.name)
            self.b.profile(target.id, target.name, "group")
            main = self.b.window

            def windows():
                return matching_windows(self.b, main.ProcessId, f"群公告-{target.name}")

            existing = windows()
            if len(existing) > 1:
                existing = wait_until(windows, lambda values: len(values) == 1)
            window = existing[0] if existing else None
            opened = window is None
            try:
                if opened:
                    button = self.b.find(
                        main,
                        lambda c: (
                            c.ControlTypeName == "ButtonControl"
                            and c.Name == "群公告"
                            and "group-notice" in c.ClassName.split()
                        ),
                    )
                    self.b.invoke(button)
                    found = wait_until(windows, lambda values: len(values) == 1)
                    if len(found) != 1:
                        raise ControlUnavailable("Announcement window is ambiguous")
                    window = found[0]

                def loaded():
                    node = self.b.snapshot(window)
                    if any(n.has_class("notice-list") for n in node.walk()):
                        result = parse_notices(node, target.id, target.name)
                        # The empty list container appears before its network rows.
                        # No native empty-state marker has been verified yet.
                        return result if result.items else None
                    return None

                return wait_until(loaded)
            finally:
                if opened and window is not None:
                    window.GetWindowPattern().Close(waitTime=0.1)
                    main.SetActive(waitTime=0.1)
                self.b.window = main
