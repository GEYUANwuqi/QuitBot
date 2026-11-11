from types import SimpleNamespace

import pytest

from quitbot_backend._engine.errors import ControlUnavailable
from quitbot_backend._engine.infrastructure.uia.history import HistoryUI
from quitbot_backend._engine.models import Node, Target, parse_history


def file_row(name="report.txt", metadata=None):
    if metadata is None:
        metadata = ["16:39", "·", "64.0\u2009B", "·", "已下载", "·", "测试账号"]
    return Node(
        class_name="record-msg-detail",
        children=(
            Node(
                class_name="record-file",
                children=(
                    Node(
                        class_name="record-file-name text-ellipsis",
                        children=tuple(Node(type="TextControl", name=c) for c in name),
                    ),
                    *(Node(type="TextControl", name=value) for value in metadata),
                ),
            ),
        ),
    )


def test_file_category_parses_known_metadata_without_inventing_direction_or_id():
    root = Node(
        children=(
            Node(
                class_name="record-msg-date",
                children=(Node(type="TextControl", name="2024/01/02"),),
            ),
            file_row("中文 report.txt"),
        )
    )
    (row,) = parse_history(root, "12345")
    assert row.kind == "file" and row.text == "中文 report.txt"
    assert row.attachments[0].name == row.text
    assert row.sender == "测试账号" and row.timestamp == "2024/01/02 16:39"
    assert row.direction == "unknown" and row.id is None


@pytest.mark.parametrize(
    "metadata",
    [
        [],
        ["16:39", "·", "64B", "·", "下载中"],
        ["16:39", "—", "64B", "·", "已下载", "·", "测试账号"],
    ],
)
def test_unknown_file_metadata_does_not_guess_sender(metadata):
    (row,) = parse_history(Node(children=(file_row(metadata=metadata),)), "12345")
    assert row.sender is None and row.direction == "unknown"
    assert row.text == "report.txt"


class Control:
    def __init__(self, *, name="", cls="", role="GroupControl", children=()):
        self.Name = name
        self.ClassName = cls
        self.ControlTypeName = role
        self.AutomationId = ""
        self.IsEnabled = True
        self.IsOffscreen = False
        self.ProcessId = 1
        self.children = list(children)
        self.Value = ""

    def GetChildren(self):
        return self.children

    def GetValuePattern(self):
        return self


@pytest.fixture(autouse=True)
def native_guards(monkeypatch):
    monkeypatch.setattr(
        "quitbot_backend._engine.infrastructure.uia.history.require_unlocked", lambda _: None
    )

    def wait_once(read, predicate=bool, **_):
        result = read()
        if not predicate(result):
            raise ControlUnavailable("Postcondition did not match")
        return result

    monkeypatch.setattr("quitbot_backend._engine.infrastructure.uia.history.wait_until", wait_once)


def provider(
    *, initial="链接", query="old", existing=True, failure=None, direct_names=True
):
    state = {"category": initial, "query": query, "created": existing, "closed": False}
    calls = []
    labels = ["全部", "图片/视频", "表情", "文件", "链接"]
    tabs = [
        Control(
            name=label if direct_names else "",
            cls="q-tabs-item",
            children=(Control(name=label, role="TextControl"),),
        )
        for label in labels
    ]
    header = Control(cls="record-header")
    search = Control(name="搜索", role="EditControl")
    search.Value = query
    panel = Control(cls="record-msg-panel")
    window = Control(name="测试群")
    window.children = [header, *tabs, search, panel]
    button = Control(name="聊天记录", role="ButtonControl")
    source = Control(name="QQ", children=(button,))
    root = Control()

    def update():
        for tab, label in zip(tabs, labels, strict=True):
            tab.ClassName = "q-tabs-item" + (
                " active" if label == state["category"] else ""
            )
        category = "file" if state["category"] == "文件" else "all"
        panel.ClassName = f"record-msg-panel record-{category}-msg"
        panel.children = (
            [
                Control(
                    name="暂无文件" if category == "file" else "暂无消息",
                    role="TextControl",
                )
            ]
            if state["query"] == "missing"
            else []
        )
        root.children = [window] if state["created"] else []

    def walk(control):
        yield control
        for child in control.GetChildren():
            yield from walk(child)

    def find(control, predicate, required=True):
        found = next((item for item in walk(control) if predicate(item)), None)
        if found is None and required:
            raise ControlUnavailable("Missing fake control")
        return found

    def invoke(control):
        nonlocal search
        if control is button:
            state["created"] = True
            calls.append("open")
        else:
            label = labels[tabs.index(control)]
            calls.append(("category", label))
            if failure == "select":
                return
            if failure == "restore" and label == initial:
                raise ControlUnavailable("Restore tab failed")
            state["category"] = label
            # Native category switches may invalidate an edit element.
            replacement = Control(name="搜索", role="EditControl")
            replacement.Value = state["query"]
            window.children[window.children.index(search)] = replacement
            search = replacement
        update()

    def send_keys(keys, **_):
        if keys == "{Ctrl}a{Back}":
            assert b.focused is search
            search.Value = ""
            state["query"] = ""
        elif keys == "{Enter}":
            state["query"] = search.Value
            calls.append(("query", state["query"]))
        update()

    def type_literal(control, value):
        assert control is search
        control.Value = value
        state["query"] = value

    def snapshot(control):
        assert control is panel
        if failure == "read":
            raise ControlUnavailable("Read failed")
        if state["query"] == "missing":
            return Node()
        name = state["query"] or "report.txt"
        return Node(children=(file_row(name),))

    def close(**_):
        state["closed"] = True
        calls.append("close")

    window.GetWindowPattern = lambda: SimpleNamespace(Close=close)
    b = SimpleNamespace(
        window=source,
        auto=SimpleNamespace(GetRootControl=lambda: root, SendKeys=send_keys),
        walk=walk,
        find=find,
        invoke=invoke,
        snapshot=snapshot,
        type_literal=type_literal,
        profile=lambda *_: None,
    )
    b.focus_control = lambda control: setattr(b, "focused", control)
    client = SimpleNamespace(
        backend=b, target=lambda _: Target("12345", "测试群"), _open=lambda _: None
    )
    update()
    return HistoryUI(client), state, calls, tabs, window, source


@pytest.mark.parametrize("initial", ["文件", "链接"])
def test_files_read_restores_existing_category_and_query(initial):
    ui, state, calls, _, _, source = provider(initial=initial)
    rows = ui.read("12345", category="files", query="report")
    assert rows[0].text == "report"
    assert state["category"] == initial and state["query"] == "old"
    assert not state["closed"] and ui.b.window is source
    assert ("query", "report") in calls and ("query", "old") in calls
    if initial == "文件":
        assert not any(call[0] == "category" for call in calls)


def test_default_all_does_not_inherit_existing_files_tab_and_reacquires_search():
    ui, state, calls, _, _, source = provider(initial="文件", direct_names=False)
    ui.read("12345")
    assert calls == [
        ("category", "全部"),
        ("query", ""),
        ("category", "文件"),
        ("query", "old"),
    ]
    assert state["category"] == "文件" and ui.b.window is source


def test_empty_files_result_is_a_success_and_prior_search_is_restored():
    ui, state, *_ = provider()
    assert ui.read("12345", category="files", query="missing") == []
    assert state["query"] == "old"


@pytest.mark.parametrize("existing", [True, False])
def test_read_failure_restores_category_query_and_backend_window(existing):
    ui, state, _, _, _, source = provider(existing=existing, failure="read")
    with pytest.raises(ControlUnavailable, match="Read failed"):
        ui.read("12345", category="files")
    assert state["category"] == "链接" and state["query"] == "old"
    assert state["closed"] is (not existing) and ui.b.window is source


def test_new_window_closed_even_when_restore_fails():
    ui, state, _, _, _, source = provider(existing=False, failure="restore")
    with pytest.raises(ControlUnavailable, match="Restore tab failed"):
        ui.read("12345", category="files")
    assert state["closed"] and ui.b.window is source


def test_category_must_actually_become_active_before_reading():
    ui, _, calls, _, _, source = provider(failure="select")
    with pytest.raises(ControlUnavailable, match="Postcondition"):
        ui.read("12345", category="files")
    assert calls == [("category", "文件")]
    assert ui.b.window is source


@pytest.mark.parametrize("category", ["images", None])
def test_unimplemented_categories_rejected_before_opening(category):
    ui, _, calls, *_ = provider()
    with pytest.raises(ValueError, match="category"):
        ui.read("12345", category=category)
    assert calls == []


def test_ambiguous_active_tabs_rejected_without_invocation():
    ui, _, calls, tabs, window, _ = provider()
    tabs[0].ClassName += " active"
    with pytest.raises(ControlUnavailable, match="exactly one"):
        ui.select_category(window, "文件")
    assert calls == []


def test_disabled_category_is_not_invoked():
    ui, _, calls, tabs, window, _ = provider()
    tabs[3].IsEnabled = False
    with pytest.raises(ControlUnavailable, match="unavailable"):
        ui.select_category(window, "文件")
    assert calls == []


def test_active_tab_change_during_read_is_rejected():
    ui, *_ = provider(initial="全部")
    with pytest.raises(ControlUnavailable, match="changed"):
        ui._loaded_state(ui.b.auto.GetRootControl().GetChildren()[0], "12345", "files")
