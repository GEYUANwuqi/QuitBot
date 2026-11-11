"""Read observed history views and preserve user-owned window state."""

import re
from contextlib import contextmanager
from datetime import date

from ...errors import ControlUnavailable, DraftNotEmpty, TargetMismatch
from ...locking import ui_transaction
from ...models import editor_has_content, parse_history, text_of
from ...uia import wait_until
from .inspection import DesktopLocked, DesktopUnavailable, require_unlocked

HISTORY_CATEGORIES = {"all": "全部", "files": "文件"}
HISTORY_TAB_LABELS = frozenset(("全部", "图片/视频", "表情", "文件", "链接"))
HISTORY_PANEL_CLASSES = {"all": "record-all-msg", "files": "record-file-msg"}


class HistoryUI:
    def __init__(self, client):
        self.client = client
        self.b = client.backend

    @contextmanager
    def _fresh(self, key):
        ui, b = self.client.control_ui, self.b
        with ui.checked(key) as target:
            if editor_has_content(b.snapshot(b.editor())):
                raise DraftNotEmpty("Existing composer draft retained")
            if ui.by_class("q-dialog-main") is not None:
                raise ControlUnavailable("Existing dialog retained")
            pid = b.window.ProcessId

            def windows():
                return [
                    w
                    for w in b.auto.GetRootControl().GetChildren()
                    if w.ProcessId == pid
                    and b.find(
                        w, lambda c: "record-header" in c.ClassName.split(), False
                    )
                    is not None
                ]

            if windows():
                raise ControlUnavailable(
                    "Existing history window retained; close it first"
                )
            original, owned = b.window, None
            try:
                b.invoke(
                    b.find(
                        b.window,
                        lambda c: (
                            c.Name == "聊天记录"
                            and c.ControlTypeName == "ButtonControl"
                        ),
                    )
                )
                candidates = wait_until(windows)
                if len(candidates) != 1 or candidates[0].Name != target.name:
                    raise TargetMismatch("History window is ambiguous")
                owned = candidates[0]
                b.window = owned
                wait_until(lambda: self._tabs(owned))
                yield target, owned, original
            finally:
                try:
                    if owned is not None:
                        require_unlocked(b.auto)
                        owned.GetWindowPattern().Close(waitTime=0.1)
                finally:
                    b.window = original

    def _select_month(self, calendar, year, month):
        b, ui = self.b, self.client.control_ui
        title = lambda: text_of(
            b.snapshot(ui.by_class("vc-title", calendar, required=True))
        )
        desired = f"{year:04}-{month:02}"
        if title() == desired:
            return
        b.invoke(ui.by_class("vc-title", calendar, required=True))
        nav = wait_until(lambda: ui.by_class("vc-nav-container", calendar))
        current_year = ui.by_class("vc-nav-title", nav, required=True).Name
        if current_year != str(year):
            b.invoke(ui.by_class("vc-nav-title", nav, required=True))
            for _ in range(20):
                nav = ui.by_class("vc-nav-container", calendar, required=True)
                label = ui.by_class("vc-nav-title", nav, required=True).Name
                match = re.fullmatch(r"(\d{4})\s*-\s*(\d{4})", label)
                if match is None:
                    raise ControlUnavailable("Unrecognized year range")
                if int(match[1]) <= year <= int(match[2]):
                    choice = b.find(
                        nav,
                        lambda c: (
                            c.Name == str(year) and "vc-nav-item" in c.ClassName.split()
                        ),
                    )
                    if (
                        not choice.IsEnabled
                        or "is-disabled" in choice.ClassName.split()
                    ):
                        raise ControlUnavailable("Requested year is disabled")
                    b.invoke(choice)
                    break
                direction = "is-left" if year < int(match[1]) else "is-right"
                arrow = b.find(
                    nav,
                    lambda c, direction=direction: (
                        "vc-nav-arrow" in c.ClassName.split()
                        and direction in c.ClassName.split()
                    ),
                )
                if not arrow.IsEnabled or "is-disabled" in arrow.ClassName.split():
                    raise ControlUnavailable("Year range navigation is disabled")
                b.invoke(arrow)
                wait_until(
                    lambda: ui.by_class("vc-nav-title", calendar, required=True).Name,
                    lambda s, label=label: s != label,
                )
            else:
                raise ControlUnavailable("Year navigation exceeded its bound")
        wait_until(
            lambda: ui.by_class("vc-nav-title", calendar, required=True).Name,
            lambda s: s == str(year),
        )
        labels = (
            "一月",
            "二月",
            "三月",
            "四月",
            "五月",
            "六月",
            "七月",
            "八月",
            "九月",
            "十月",
            "十一月",
            "十二月",
        )
        nav = ui.by_class("vc-nav-container", calendar, required=True)
        # QQ reports 2000 in every month Name. Read the year independently above.
        selected = b.find(
            nav,
            lambda c: (
                c.Name == labels[month - 1] + " 2000"
                and "vc-nav-item" in c.ClassName.split()
            ),
        )
        if not selected.IsEnabled or "is-disabled" in selected.ClassName.split():
            raise ControlUnavailable("Requested month is disabled")
        b.invoke(selected)
        wait_until(title, lambda s: s == desired)

    def calendar(self, key, year, month):
        if (
            type(year) is not int
            or type(month) is not int
            or not 1900 <= year <= 2100
            or not 1 <= month <= 12
        ):
            raise ValueError("Use integer year 1900..2100 and month 1..12")
        b, ui = self.b, self.client.control_ui
        with self._fresh(key) as (target, window, _):
            self.select_category(window, "全部")
            b.invoke(ui.by_class("record-filter-btn", window, required=True))
            b.invoke(ui.by_class("date-picker", window, required=True))
            calendar = wait_until(lambda: ui.by_class("qq-calendar", window))
            self._select_month(calendar, year, month)
            days = tuple(
                {
                    "label": c.Name,
                    "enabled": bool(
                        c.IsEnabled and "is-disabled" not in c.ClassName.split()
                    ),
                }
                for c in b.walk(calendar)
                if c.ControlTypeName == "ButtonControl"
                and re.fullmatch(r"\d{4}年\d{1,2}月\d{1,2}日星期.", c.Name)
            )
            return {
                "conversation_id": target.id,
                "month": f"{year:04}-{month:02}",
                "days": days,
            }

    def locate_image(self, key, on_date, *, expected_message_id):
        if not isinstance(on_date, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", on_date
        ):
            raise ValueError("on_date must be YYYY-MM-DD")
        day = date.fromisoformat(on_date).strftime("%Y/%m/%d")
        if not str(expected_message_id).strip():
            raise ValueError("Provide the expected local UI message key")
        b, ui = self.b, self.client.control_ui
        with self._fresh(key) as (target, window, original):
            if target.kind != "private":
                raise ValueError("This locate path was validated for private history")
            self.select_category(window, "图片/视频")

            def loaded_images():
                panel = ui.by_class("record-media-msg", window)
                matches, current = [], None
                if panel is None:
                    return matches
                for c in b.walk(panel):
                    if "record-msg-date" in c.ClassName.split():
                        current = text_of(b.snapshot(c))
                    elif (
                        current == day
                        and c.ClassName == "image"
                        and c.ControlTypeName == "ImageControl"
                    ):
                        matches.append(c)
                return matches

            # The category and its empty panel can precede the loaded thumbnails.
            matches = wait_until(loaded_images)
            if len(matches) != 1:
                raise ControlUnavailable(
                    "Date must identify exactly one loaded image thumbnail"
                )
            from comtypes.gen.UIAutomationClient import IUIAutomationElement3

            matches[0].Element.QueryInterface(IUIAutomationElement3).ShowContextMenu()
            try:
                b.invoke(ui._menu_item(wait_until(ui.navigation._menu), "定位到聊天"))
                b.window = original
                wait_until(lambda: b.header().Name, lambda s: s == target.name)

                def located():
                    return [
                        m
                        for m in self.client._read(target)
                        if m.id == str(expected_message_id)
                        and m.kind in {"image", "mixed"}
                        and any(a.kind == "image" for a in m.attachments)
                    ]

                messages = wait_until(located)
                row = b.find(
                    b.window,
                    lambda c: (
                        c.AutomationId == str(expected_message_id)
                        and "ml-item" in c.ClassName.split()
                    ),
                )
                if len(messages) != 1 or row.IsOffscreen:
                    raise TargetMismatch(
                        "Expected source image was not brought into view"
                    )
                return messages[0]
            finally:
                b.window = window
                ui._dismiss_menu()

    def _tabs(self, window):
        tabs = {}
        for control in self.b.walk(window):
            if "q-tabs-item" not in control.ClassName.split():
                continue
            labels = {
                item.Name
                for item in (control, *control.GetChildren())
                if item.Name in HISTORY_TAB_LABELS
            }
            if len(labels) != 1:
                raise ControlUnavailable("Unknown or ambiguous history tab label")
            label = labels.pop()
            if label in tabs:
                raise ControlUnavailable("Duplicate history category controls")
            tabs[label] = control
        return tabs

    def active_category(self, window):
        active = [
            label
            for label, control in self._tabs(window).items()
            if "active" in control.ClassName.split()
        ]
        if len(active) != 1:
            raise ControlUnavailable("History requires exactly one active category")
        return active[0]

    def select_category(self, window, label):
        if label not in HISTORY_TAB_LABELS:
            raise ValueError("Unsupported native history category")
        if self.active_category(window) == label:
            return
        control = self._tabs(window).get(label)
        if control is None or not control.IsEnabled or control.IsOffscreen:
            raise ControlUnavailable("History category is unavailable")
        self.b.invoke(control)
        wait_until(lambda: self.active_category(window), lambda value: value == label)

    def _search(self, window):
        controls = [
            control
            for control in self.b.walk(window)
            if control.Name == "搜索" and control.ControlTypeName == "EditControl"
        ]
        if len(controls) != 1:
            raise ControlUnavailable("History requires exactly one search field")
        return controls[0]

    def _query(self, window):
        return self._search(window).GetValuePattern().Value

    def _apply_query(self, window, value):
        # Category changes can replace the edit control, so resolve it afresh.
        search = self._search(window)
        self.b.focus_control(search)
        self.b.auto.SendKeys("{Ctrl}a{Back}", waitTime=0.05)
        if value:
            self.b.type_literal(search, value)
        wait_until(lambda: self._query(window), lambda current: current == value)
        self.b.auto.SendKeys("{Enter}", waitTime=0.1)

    def _panel(self, window, category):
        panels = [
            control
            for control in self.b.walk(window)
            if "record-msg-panel" in control.ClassName.split()
            and HISTORY_PANEL_CLASSES[category] in control.ClassName.split()
        ]
        if len(panels) > 1:
            raise ControlUnavailable("Ambiguous history result panel")
        return panels[0] if panels else None

    def _loaded_state(self, window, target_id, category):
        if self.active_category(window) != HISTORY_CATEGORIES[category]:
            raise ControlUnavailable("History category changed during the read")
        panel = self._panel(window, category)
        if panel is None:
            return [], False
        records = parse_history(self.b.snapshot(panel), target_id)
        empty = (
            self.b.find(
                panel,
                lambda control: (
                    control.Name == ("暂无文件" if category == "files" else "暂无消息")
                ),
                required=False,
            )
            is not None
        )
        return records, empty

    def _filters(self, window, sender, on_date):
        ui, b = self.client.control_ui, self.b
        b.invoke(ui.by_class("record-filter-btn", window, required=True))
        if sender is not None:
            b.invoke(ui.by_class("group-member-filter__form", window, required=True))
            menu = wait_until(lambda: ui.by_class("filter-member-list", window))
            candidates = [
                c
                for c in b.walk(menu)
                if c.ControlTypeName == "MenuItemControl" and c.Name == sender
            ]
            if len(candidates) != 1:
                raise ControlUnavailable(
                    "Sender is absent or ambiguous in loaded filter choices"
                )
            b.invoke(candidates[0])
        if on_date is not None:
            b.invoke(ui.by_class("date-picker", window, required=True))
            calendar = wait_until(lambda: ui.by_class("qq-calendar", window))
            month = text_of(
                b.snapshot(ui.by_class("vc-title", calendar, required=True))
            )
            if not re.fullmatch(r"\d{4}-\d{2}", month):
                raise ControlUnavailable("Unrecognized calendar month")
            year, current_month = map(int, month.split("-"))
            delta = (on_date.year - year) * 12 + on_date.month - current_month
            if abs(delta) > 24:
                raise ValueError("Date filter navigation is bounded to 24 months")
            for _ in range(abs(delta)):
                old = month
                arrow = b.find(
                    calendar,
                    lambda c: (
                        "vc-arrow" in c.ClassName.split()
                        and ("is-left" if delta < 0 else "is-right")
                        in c.ClassName.split()
                    ),
                )
                if "is-disabled" in arrow.ClassName.split():
                    raise ControlUnavailable("Calendar navigation is disabled")
                b.invoke(arrow)
                month = wait_until(
                    lambda: text_of(
                        b.snapshot(ui.by_class("vc-title", calendar, required=True))
                    ),
                    lambda value, old=old: value != old,
                )
            if month != f"{on_date.year:04}-{on_date.month:02}":
                raise ControlUnavailable("Calendar did not reach the requested month")
            prefix = f"{on_date.year}年{on_date.month}月{on_date.day}日星期"
            days = [
                c
                for c in b.walk(calendar)
                if c.ControlTypeName == "ButtonControl" and c.Name.startswith(prefix)
            ]
            if (
                len(days) != 1
                or not days[0].IsEnabled
                or "is-disabled" in days[0].ClassName.split()
            ):
                raise ControlUnavailable("Requested calendar day is unavailable")
            b.invoke(days[0])

    def read(
        self,
        key,
        *,
        query=None,
        limit=100,
        pages=1,
        category="all",
        sender=None,
        on_date=None,
    ):
        if sender is not None and (
            not isinstance(sender, str)
            or not sender.strip()
            or len(sender) > 100
            or any(ord(c) < 32 for c in sender)
        ):
            raise ValueError("Sender must be an exact display name")
        selected_date = None
        if on_date is not None:
            if not isinstance(on_date, str) or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}", on_date
            ):
                raise ValueError("on_date must be YYYY-MM-DD")
            selected_date = date.fromisoformat(on_date)
        filtered = sender is not None or selected_date is not None
        if category not in HISTORY_CATEGORIES:
            raise ValueError("History category must be all or files")
        if not 1 <= limit <= 1000 or query is not None and not query.strip():
            raise ValueError("Provide limit 1..1000 and a nonempty query")
        if not 1 <= pages <= 50:
            raise ValueError("pages must be 1..50")
        target = self.client.target(key)
        if filtered and category != "all":
            raise ValueError("Validated filters require history category all")
        if sender is not None and target.kind != "group":
            raise ValueError("Sender filtering requires group history")
        b = self.b
        with ui_transaction():
            self.client._open(target)
            b.profile(target.id, target.name, target.kind)
            pid = b.window.ProcessId

            def candidates():
                return [
                    window
                    for window in b.auto.GetRootControl().GetChildren()
                    if window.ProcessId == pid
                    and window.Name == target.name
                    and b.find(
                        window,
                        lambda control: "record-header" in control.ClassName.split(),
                        required=False,
                    )
                ]

            existing = candidates()
            if filtered and existing:
                raise ControlUnavailable(
                    "Existing history window retained; close it before filtered reading"
                )
            if not existing:
                b.invoke(
                    b.find(
                        b.window,
                        lambda control: (
                            control.Name == "聊天记录"
                            and control.ControlTypeName == "ButtonControl"
                        ),
                    )
                )
            windows = wait_until(candidates)
            if len(windows) != 1:
                raise TargetMismatch("Ambiguous QQ history window")
            window = windows[0]
            original_window = b.window
            b.window = window
            old_category = None
            old_query = None
            try:
                old_category = self.active_category(window)
                old_query = self._query(window)
                self.select_category(window, HISTORY_CATEGORIES[category])
                if filtered and target.kind == "private":
                    # Private history exposes only a date filter. Set it before
                    # searching so a no-match query cannot hide calendar choices.
                    self._filters(window, sender, selected_date)
                current_query = wait_until(
                    lambda: self._query(window), lambda value: isinstance(value, str)
                )
                if current_query != (query or ""):
                    self._apply_query(window, query or "")
                if filtered and target.kind == "group":
                    self._filters(window, sender, selected_date)

                def loaded_state():
                    return self._loaded_state(window, target.id, category)

                records, _ = wait_until(
                    loaded_state,
                    lambda state: (
                        state[1]
                        or bool(state[0])
                        and (
                            sender is None
                            or all(row.sender == sender for row in state[0])
                        )
                        and (
                            selected_date is None
                            or all(
                                (row.timestamp or "").split(" ", 1)[0]
                                == selected_date.strftime("%Y/%m/%d")
                                for row in state[0]
                            )
                        )
                        and (
                            query is None
                            or all(
                                query.casefold()
                                in (
                                    row.text
                                    + " "
                                    + (row.quote.text if row.quote else "")
                                    + " "
                                    + (row.sender or "")
                                ).casefold()
                                for row in state[0]
                            )
                        )
                    ),
                )
                for _ in range(pages - 1):
                    if len(records) >= limit or not records:
                        break
                    panel = self._panel(window, category)
                    if panel is None:
                        raise ControlUnavailable("History result panel disappeared")
                    scroller = b.find(
                        panel,
                        lambda control: "q-scroll-view" in control.ClassName.split(),
                        required=False,
                    )
                    scroll = (
                        scroller.GetPattern(b.auto.PatternId.ScrollPattern)
                        if scroller is not None
                        else None
                    )
                    if scroll is None or not scroll.VerticallyScrollable:
                        break
                    before = records
                    before_size = len(before)
                    scroll.SetScrollPercent(-1, 100, waitTime=0.7)
                    try:
                        records, _ = wait_until(
                            loaded_state,
                            lambda state, size=before_size: len(state[0]) > size,
                            timeout=3,
                        )
                    except ControlUnavailable:
                        records, _ = loaded_state()
                        if records != before:
                            raise ControlUnavailable(
                                "History changed during pagination; retry the read"
                            ) from None
                        break
                    if records[: len(before)] != before:
                        raise ControlUnavailable(
                            "History changed during pagination; refusing ambiguous rows"
                        )
                return records[:limit]
            finally:
                try:
                    require_unlocked(b.auto)
                    # Restore tabs first: switching categories can rebuild the query.
                    if old_category is not None:
                        self.select_category(window, old_category)
                    if old_query is not None and self._query(window) != old_query:
                        self._apply_query(window, old_query)
                except (DesktopLocked, DesktopUnavailable):
                    pass
                finally:
                    try:
                        if not existing:
                            require_unlocked(b.auto)
                            window.GetWindowPattern().Close(waitTime=0.05)
                    except (DesktopLocked, DesktopUnavailable):
                        pass
                    finally:
                        b.window = original_window
