"""Read-only navigation into QQ's hover menus and secondary views."""

import re
import time
from contextlib import ExitStack, contextmanager
from dataclasses import replace

from quitbot_backend._engine.domain.controls import (
    FileSortChoice,
    FileSortState,
    GroupFileItem,
    GroupFilesView,
    MenuAction,
    MessageMenuInfo,
    ReactionChoice,
    ReactionControlInfo,
    ReactionPanel,
    SubmenuInfo,
)
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    DraftNotEmpty,
    TargetMismatch,
)
from quitbot_backend._engine.models import editor_has_content, text_of
from quitbot_backend._engine.uia import wait_until

from .inspection import DesktopLocked, hover_control, require_unlocked

FILE_SORT_LABELS = {
    "newest": "按最新时间",
    "oldest": "按最旧时间",
    "size_desc": "按从大到小",
    "size_asc": "按从小到大",
}


def file_sort_state(group_id, rows, *, expected_keys=None):
    """Retain indeterminate values instead of guessing the native checked option."""
    choices = tuple(
        FileSortChoice(key, FILE_SORT_LABELS[key], state, enabled)
        for key, state, enabled in rows
    )
    selected = [c.key for c in choices if c.selected is True]
    expected_keys = set(FILE_SORT_LABELS if expected_keys is None else expected_keys)
    complete = {c.key for c in choices} == expected_keys and len(choices) == len(expected_keys)
    current = (
        selected[0]
        if complete
        and len(selected) == 1
        and all(c.selected is not None for c in choices)
        else None
    )
    return FileSortState(group_id, current, choices)


def more_reaction_control(items, backend):
    """Recognize image or text shortcuts and the unique More icon."""
    if len(items) != 7:
        return None
    direct = []
    more = []
    for item in items:
        if item.ControlTypeName != "GroupControl" or not item.GetPattern(
            backend.auto.PatternId.InvokePattern
        ):
            return None
        nodes = tuple(backend.walk(item))
        images = [x for x in nodes if x.ControlTypeName == "ImageControl"]
        if not images:
            texts = [x for x in nodes if x.ControlTypeName == "TextControl"]
            invokers = [
                x for x in nodes if x.ControlTypeName == "GroupControl"
                and x.GetPattern(backend.auto.PatternId.InvokePattern)
            ]
            if len(texts) == 1 and texts[0].Name and len(invokers) == 2:
                direct.append(item)
                continue
            return None
        if len(images) != 1:
            return None
        (
            direct
            if images[0].GetPattern(backend.auto.PatternId.InvokePattern)
            else more
        ).append(item)
    return more[0] if len(direct) == 6 and len(more) == 1 else None


def parse_file_view(node, group_id, group_name, *, query=None):
    strings = [n.name for n in node.walk() if n.type == "TextControl" and n.name]
    expected = f"群文件 - {group_name}"
    if expected not in strings:
        raise TargetMismatch("Group file page does not show the expected group title")
    if query:
        panels = [n for n in node.walk() if n.has_class("search-file-prop-menu")]
        if len(panels) != 1:
            raise ControlUnavailable("File search suggestion panel is not ready")
        rows = [
            n
            for n in panels[0].walk()
            if n.type == "MenuItemControl"
            and any(x.has_class("search-file-icon-area") for x in n.walk())
        ]
        empty = any(n.has_class("search-result-empty") for n in panels[0].walk())
        if not rows and not empty:
            raise ControlUnavailable("File search results are not ready")
        if any(query.casefold() not in row.name.casefold() for row in rows):
            raise ControlUnavailable("File search still shows another query's results")
        return GroupFilesView(
            group_id,
            expected,
            tuple(
                GroupFileItem(i, row.name, None, None, None, None)
                for i, row in enumerate(rows)
            ),
            None,
            None,
            scope="search_suggestions",
            query=query,
        )
    rows = []
    for item in node.walk():
        if not item.has_class("file-container"):
            continue
        nodes = tuple(item.walk())

        def field(cls, nodes=nodes):
            return next((text_of(n) for n in nodes if n.has_class(cls)), None)

        details = [text_of(n) for n in nodes if n.has_class("file-details-info")]
        date = next(
            (
                n.name
                for n in nodes
                if n.type == "TextControl"
                and re.fullmatch(r"\d{1,4}[/-]\d{1,2}(?:[/-]\d{1,2})?", n.name)
            ),
            None,
        )
        rows.append(
            GroupFileItem(
                len(rows),
                field("file-name") or "",
                details[0] if details else None,
                details[1] if len(details) > 1 else None,
                date,
                field("file-send-desc"),
            )
        )
    total = next((s for s in strings if re.fullmatch(r"共\s*\d+\s*个文件", s)), None)
    capacity = next(
        (s for s in strings if " / " in s and re.search(r"(?:Bytes|[KMGT]?B)\s*/", s)),
        None,
    )
    return GroupFilesView(group_id, expected, tuple(rows), total, capacity)


class SecondaryUI:
    def __init__(self, client):
        self.client = client
        self.ui = client.control_ui
        self.b = client.backend

    def _app_button(self):
        return self.b.find(
            self.b.header().GetParentControl().GetParentControl(),
            lambda x: x.ControlTypeName == "ButtonControl" and x.Name == "群应用",
        )

    @contextmanager
    def apps(self, target):
        if target.kind != "group":
            raise ValueError("Group application menus require a group")
        button = self._app_button()
        with ExitStack() as stack:
            if button.IsKeyboardFocusable:
                require_unlocked(self.b.auto)
                self.b.focus_control(button)
                self.b.auto.SendKeys("{Enter}", waitTime=0.1)
            else:
                stack.enter_context(hover_control(self.b, button))
            menu = wait_until(lambda: self.ui.by_class("bar-more-menu-inner"))
            try:
                yield menu
            finally:
                self.ui._dismiss_menu()

    def application_menu(self, key):
        with self.ui.checked(key) as target, self.apps(target) as menu:
            return self.ui._actions(menu)

    def member_submenu(self, key, member, entry):
        with self.ui.checked(key) as target:
            if target.kind != "group":
                raise ValueError("Group member submenu requires a group")
            with self.ui.roster(member):
                menu = self.ui._context(self.ui._member_row(member))
                try:
                    item = self.ui._menu_item(menu, entry)
                    pattern = item.GetPattern(
                        self.b.auto.PatternId.ExpandCollapsePattern
                    )
                    if pattern:
                        pattern.Expand(waitTime=0.1)
                        mechanism = "ExpandCollapsePattern.Expand"
                        return self._submenu(entry, mechanism)
                    if "q-context-sub-menu__item" not in item.ClassName.split():
                        raise ControlUnavailable(
                            "No cascade marker; business leaf is not invoked"
                        )
                    if item.IsKeyboardFocusable:
                        self.b.focus_control(item)
                        self.b.auto.SendKeys("{Right}", waitTime=0.1)
                        submenu = self.ui.by_class("q-context-sub-menu__container")
                        if submenu is not None:
                            return self._submenu(entry, "SetFocus + Right")
                    with hover_control(self.b, item):
                        return self._submenu(entry, "UIA-derived hover")
                finally:
                    self.ui._dismiss_menu()

    def _submenu(self, entry, mechanism):
        submenu = wait_until(lambda: self.ui.by_class("q-context-sub-menu__container"))
        actions = wait_until(lambda: self.ui._actions(submenu))
        if "禁言" in entry:
            actions = [replace(action, risk="moderation") for action in actions]
        return SubmenuInfo(entry, mechanism, tuple(actions))

    @contextmanager
    def files_view(self, key, query=None):
        if query is not None and (
            not isinstance(query, str)
            or len(query) > 100
            or any(ord(c) < 32 for c in query)
        ):
            raise ValueError("Invalid file query")
        with self.ui.checked(key) as target:
            pid = self.b.main_window.ProcessId

            def windows():
                return [
                    w
                    for w in self.b.auto.GetRootControl().GetChildren()
                    if w.ProcessId == pid and w.Name == "群文件"
                ]

            existing = {w.NativeWindowHandle for w in windows()}
            with self.apps(target) as menu:
                self.b.invoke(self.ui._menu_item(menu, "群文件"))

            def matching():
                return [
                    w
                    for w in windows()
                    if self.b.find(
                        w,
                        lambda x: (
                            x.ControlTypeName == "TextControl"
                            and x.Name == f"群文件 - {target.name}"
                        ),
                        False,
                    )
                    is not None
                ]

            matches = wait_until(matching)
            if len(matches) != 1:
                raise TargetMismatch("Ambiguous group file page")
            window = matches[0]
            original = self.b.window
            self.b.window = window
            old = search = None
            try:
                wait_until(lambda: self.ui.by_class("file-area-container", window))
                search_root = wait_until(lambda: self.ui.by_class("q-search", window))
                searches = [
                    x
                    for x in self.b.walk(search_root)
                    if x.ControlTypeName == "EditControl"
                ]
                if len(searches) != 1:
                    raise ControlUnavailable(
                        "File search input is missing or ambiguous"
                    )
                search = searches[0]
                value = search.GetValuePattern()
                old = value.Value
                if old != (query or ""):
                    value.SetValue(query or "", waitTime=0.5)
                yield target, window
            finally:
                try:
                    require_unlocked(self.b.auto)
                    try:
                        # Folder navigation rebuilds/removes the old search element.
                        # Resolve its current counterpart rather than touching a
                        # stale provider reference during context cleanup.
                        root = self.ui.by_class("q-search", window)
                        inputs = (
                            [
                                x
                                for x in self.b.walk(root)
                                if x.ControlTypeName == "EditControl"
                            ]
                            if root is not None
                            else []
                        )
                        value = (
                            inputs[0].GetValuePattern() if len(inputs) == 1 else None
                        )
                        if old is not None and value is not None and value.Value != old:
                            value.SetValue(old, waitTime=0.1)
                        elif old and value is None:
                            raise ControlUnavailable(
                                "Return to the group-file root before restoring its search"
                            )
                        if not old:
                            self.ui._dismiss_menu()
                    finally:
                        # A failed search restore must not strand a window that
                        # this context created. Existing user windows remain open.
                        if window.NativeWindowHandle not in existing:
                            window.GetWindowPattern().Close(waitTime=0.05)
                except DesktopLocked:
                    pass
                finally:
                    self.b.window = original

    def group_files(self, key, *, query=None, order=None):
        if order is not None and order not in FILE_SORT_LABELS:
            raise ValueError("File order must be newest/oldest/size_desc/size_asc")
        if order is not None and query is not None:
            raise ValueError("File search suggestions cannot be sorted as root rows")
        with (
            self.files_view(key, query) as (target, window),
            self.temporary_file_order(target.id, order),
        ):
            return self._read_file_view(target, window, query, order)

    def _read_file_view(self, target, window, query, order):
        # QQ may publish the window title before its file rows.
        def read():
            try:
                return parse_file_view(
                    self.b.snapshot(window), target.id, target.name, query=query
                )
            except ControlUnavailable:
                return None

        result = wait_until(
            read,
            lambda view: (
                view is not None
                and (
                    bool(view.items)
                    or view.total_text is not None
                    or view.scope == "search_suggestions"
                    or self.b.find(
                        window,
                        lambda x: (
                            x.Name in ("暂无文件", "没有找到相关文件", "无搜索结果")
                        ),
                        False,
                    )
                    is not None
                )
            ),
        )
        time.sleep(0.15)
        result = wait_until(read) if result.items else result
        return replace(result, order=order)

    def _sort_menu(self):
        button = wait_until(lambda: self.ui.by_class(
            "q-pulldown-menu-button", self.b.window
        ))
        pattern = button.GetExpandCollapsePattern()
        if pattern is None:
            raise ControlUnavailable("File sort has no ExpandCollapsePattern")
        if pattern.ExpandCollapseState != 1:
            pattern.Expand(waitTime=0.1)
        menu = wait_until(
            lambda: self.ui.by_class("q-pulldown-menu-panel", self.b.window)
        )
        return pattern, menu

    def _sort_state(self, group_id):
        _, menu = self._sort_menu()
        rows = []
        for c in self.b.walk(menu):
            if c.ControlTypeName not in ("CheckBoxControl", "MenuItemControl") or c.GetTogglePattern() is None:
                continue
            keys = [key for key, label in FILE_SORT_LABELS.items() if label == c.Name]
            if len(keys) != 1:
                raise ControlUnavailable("Unrecognized file sort option")
            toggle = c.GetTogglePattern()
            state = toggle.ToggleState if toggle else None
            rows.append((keys[0], state == 1 if state in (0, 1) else None, c.IsEnabled))
        return file_sort_state(group_id, rows, expected_keys=getattr(self.b.auto, "file_sort_keys", None))

    def _set_file_order(self, group_id, order):
        state = self._sort_state(group_id)
        if state.current is None:
            raise ControlUnavailable("Native file order is ambiguous")
        if state.current != order:
            _, menu = self._sort_menu()
            leaf = self.b.find(
                menu,
                lambda c: (
                    c.ControlTypeName in ("CheckBoxControl", "MenuItemControl")
                    and c.Name == FILE_SORT_LABELS[order]
                ),
            )
            toggle = leaf.GetTogglePattern()
            if not leaf.IsEnabled or toggle is None:
                raise ControlUnavailable("File sort selection is unavailable")
            toggle.Toggle(waitTime=0.1)
            wait_until(
                lambda: self._sort_state(group_id).current, lambda value: value == order
            )
        pattern, _ = self._sort_menu()
        pattern.Collapse(waitTime=0.05)

    @contextmanager
    def temporary_file_order(self, group_id, order):
        if order is None:
            yield
            return
        original = self._sort_state(group_id).current
        if original is None:
            raise ControlUnavailable("Cannot restore an unknown original file order")
        try:
            self._set_file_order(group_id, order)
            yield
        finally:
            self._set_file_order(group_id, original)

    def file_sort_state(self, key):
        with self.files_view(key) as (target, _):
            pattern, _ = self._sort_menu()
            try:
                return self._sort_state(target.id)
            finally:
                pattern.Collapse(waitTime=0.05)

    def file_actions(self, key, row_index, *, query=None):
        if query:
            raise ValueError(
                "Search suggestions have no file-row context; inspect root rows instead"
            )
        if (
            isinstance(row_index, bool)
            or not isinstance(row_index, int)
            or row_index < 0
        ):
            raise ValueError("row_index must be a nonnegative UI row ordinal")
        with self.files_view(key, query) as (_, window):
            rows = wait_until(
                lambda: [
                    x
                    for x in self.b.walk(window)
                    if "file-container" in x.ClassName.split()
                ]
            )
            if row_index >= len(rows):
                raise ControlUnavailable("File row is not loaded")
            row = rows[row_index]
            with hover_control(self.b, row):
                checkbox = wait_until(
                    lambda: self.b.find(
                        row, lambda x: x.ControlTypeName == "CheckBoxControl", False
                    )
                )
                if not checkbox.IsKeyboardFocusable:
                    raise ControlUnavailable(
                        "File checkbox has no keyboard focus; refusing selection as a fallback"
                    )
                menu = self.ui._context(checkbox)
                try:
                    return self.ui._actions(menu)
                finally:
                    self.ui._dismiss_menu()

    def file_sort_options(self, key):
        with self.files_view(key) as (_, window):
            button = wait_until(lambda: self.ui.by_class("q-pulldown-menu-button", window))
            pattern = button.GetPattern(self.b.auto.PatternId.ExpandCollapsePattern)
            if pattern is None:
                raise ControlUnavailable("File sort has no ExpandCollapsePattern")
            expanded = pattern.ExpandCollapseState == 1
            if not expanded:
                pattern.Expand(waitTime=0.1)
            try:
                menu = wait_until(
                    lambda: self.ui.by_class("q-pulldown-menu-panel", window)
                )
                return [
                    MenuAction(x.Name, x.IsEnabled, "local_sort_option")
                    for x in self.b.walk(menu)
                    if x.ControlTypeName in ("CheckBoxControl", "MenuItemControl") and x.GetTogglePattern() is not None
                ]
            finally:
                if not expanded:
                    require_unlocked(self.b.auto)
                    pattern.Collapse(waitTime=0.05)

    def message_secondary(self, key, message_id):
        with self.ui.checked(key):
            menu = self.ui.message_menu(key, message_id)
            try:
                wait_until(lambda: self.ui._actions(menu))
                controls = []
                for node in self.b.walk(menu):
                    if "menu-stickers-item" in node.ClassName.split():
                        controls.append(
                            ReactionControlInfo(
                                node.ControlTypeName,
                                node.Name,
                                node.ClassName,
                                bool(
                                    node.GetPattern(self.b.auto.PatternId.InvokePattern)
                                ),
                            )
                        )
                return MessageMenuInfo(tuple(self.ui._actions(menu)), tuple(controls))
            finally:
                self.ui._dismiss_menu()

    def reaction_palette(self, key, message_id):
        with self.ui.checked(key) as target:
            if editor_has_content(self.b.snapshot(self.b.editor())):
                raise DraftNotEmpty(
                    "Close the existing draft before inspecting the reaction palette"
                )
            menu = self.ui.message_menu(key, message_id)
            try:
                more = wait_until(
                    lambda: more_reaction_control(
                        [
                            x
                            for x in self.b.walk(menu)
                            if "menu-stickers-item" in x.ClassName.split()
                        ],
                        self.b,
                    ),
                    timeout=3,
                )
                self.b.invoke(more)
                panel = wait_until(lambda: self.ui.by_class("menu-stickers-panel"))
                nodes = wait_until(
                    lambda: [
                        x
                        for x in self.b.walk(panel)
                        if "stickers-list-item" in x.ClassName.split()
                    ]
                )
                choices = []
                for index, node in enumerate(nodes):
                    snap = self.b.snapshot(node)
                    choices.append(
                        ReactionChoice(
                            index,
                            text_of(snap) or None,
                            "image"
                            if any(x.type == "ImageControl" for x in snap.walk())
                            else "text",
                            bool(node.GetPattern(self.b.auto.PatternId.InvokePattern)),
                            node.IsOffscreen,
                        )
                    )
                return ReactionPanel(str(message_id), tuple(choices))
            finally:
                self.ui._dismiss_menu()
                require_unlocked(self.b.auto)
                if self.ui.by_class("menu-stickers-panel") is not None:
                    if editor_has_content(self.b.snapshot(self.b.editor())):
                        raise DraftNotEmpty(
                            "Draft appeared during inspection; reaction panel retained"
                        )
                    # This QQ build ignores Escape for the More panel. Opening
                    # the verified header's profile dismisses it without a
                    # pointer click or invoking any reaction choice.
                    self.b.profile(target.id, target.name, target.kind)
                    wait_until(
                        lambda: self.ui.by_class("menu-stickers-panel"),
                        lambda panel: panel is None,
                    )
