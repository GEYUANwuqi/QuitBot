import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.adapters.onebot.api import OneBotAdapter
from quitbot_backend._engine.domain.controls import (
    GroupFilesView,
    MessageMenuInfo,
)
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    DraftNotEmpty,
    TargetMismatch,
)
from quitbot_backend._engine.infrastructure.uia.secondary import (
    SecondaryUI,
    more_reaction_control,
    parse_file_view,
)
from quitbot_backend._engine.models import Node, Target


def text(name):
    return Node(type="TextControl", name=name)


def file_row(name):
    return Node(
        class_name="file-container",
        children=(
            Node(class_name="file-name", children=(text(name),)),
            Node(class_name="file-details-info", children=(text("64.0 B"),)),
            Node(class_name="file-details-info", children=(text("· 2 次"),)),
            text("10/25"),
            Node(class_name="file-send-desc", children=(text("来自 tester"),)),
        ),
    )


def page(*nodes):
    return Node(
        children=(
            text("群文件 - 测试群"),
            *nodes,
            text("共 2 个文件"),
            text("128 Bytes / 10 GB"),
        )
    )


def suggestion(name):
    return Node(
        type="MenuItemControl",
        name=name,
        automation_id="random-ui-id",
        children=(Node(class_name="search-file-icon-area"),),
    )


def test_file_view_preserves_duplicate_rows_without_inventing_server_ids():
    view = parse_file_view(
        page(file_row("same.txt"), file_row("same.txt")), "12345", "测试群"
    )
    assert [row.row_index for row in view.items] == [0, 1]
    assert [row.name for row in view.items] == ["same.txt", "same.txt"]
    assert all(row.id is None for row in view.items)
    assert view.items[0].size_text == "64.0 B" and view.items[0].date_text == "10/25"
    assert view.total_text == "共 2 个文件" and not view.complete
    with pytest.raises(TargetMismatch):
        parse_file_view(page(), "12345", "other")


def test_search_reads_popup_instead_of_stale_root_and_does_not_invent_metadata():
    panel = Node(
        class_name="search-file-prop-menu",
        children=(suggestion("found.txt"), suggestion("found.txt")),
    )
    view = parse_file_view(
        page(file_row("stale.txt"), panel), "12345", "测试群", query="found"
    )
    assert view.scope == "search_suggestions" and view.query == "found"
    assert len(view.items) == 2 and all(row.name == "found.txt" for row in view.items)
    assert all(row.size_text is None and row.id is None for row in view.items)
    assert view.total_text is None and view.capacity_text is None
    with pytest.raises(ControlUnavailable):
        parse_file_view(page(panel), "12345", "测试群", query="other")


def test_empty_search_is_empty_even_when_root_still_contains_files():
    panel = Node(
        class_name="search-file-prop-menu",
        children=(
            Node(class_name="search-result-empty", children=(text("暂无搜索结果"),)),
        ),
    )
    view = parse_file_view(
        page(file_row("stale.txt"), panel), "12345", "测试群", query="absent"
    )
    assert view.items == () and not view.complete
    with pytest.raises(ControlUnavailable):
        parse_file_view(page(file_row("stale.txt")), "12345", "测试群", query="absent")


def test_more_navigation_rejects_partial_or_ambiguous_shortcut_layouts():
    def item(direct):
        image = SimpleNamespace(
            ControlTypeName="ImageControl", GetPattern=lambda _: direct
        )
        return SimpleNamespace(
            ControlTypeName="GroupControl", GetPattern=lambda _: True, children=(image,)
        )

    backend = SimpleNamespace(
        auto=SimpleNamespace(PatternId=SimpleNamespace(InvokePattern=1)),
        walk=lambda n: [n, *n.children],
    )
    items = [item(True) for _ in range(6)] + [item(False)]
    assert more_reaction_control(items, backend) is items[-1]
    assert more_reaction_control(items[-1:], backend) is None
    assert more_reaction_control([item(True) for _ in range(7)], backend) is None
    assert (
        more_reaction_control(
            [item(True) for _ in range(5)] + [item(False), item(False)], backend
        )
        is None
    )
    text_item = SimpleNamespace(
        ControlTypeName="GroupControl", GetPattern=lambda _: True,
        children=(
            SimpleNamespace(ControlTypeName="GroupControl", GetPattern=lambda _: True),
            SimpleNamespace(ControlTypeName="TextControl", Name="🎉"),
        ),
    )
    mixed = [items[0], text_item, *items[2:]]
    assert more_reaction_control(mixed, backend) is items[-1]
    text_item.children = text_item.children[1:]
    assert more_reaction_control(mixed, backend) is None


def test_member_submenu_refuses_to_invoke_a_business_leaf():
    calls = []

    @contextmanager
    def checked(key):
        yield Target("12345", "测试群")

    @contextmanager
    def roster(name):
        yield None

    item = SimpleNamespace(
        GetPattern=lambda _: None,
        ClassName="q-context-menu-item",
        IsKeyboardFocusable=False,
    )
    ui = SimpleNamespace(
        checked=checked,
        roster=roster,
        _member_row=lambda _: None,
        _context=lambda _: None,
        _menu_item=lambda *_: item,
        _dismiss_menu=lambda: calls.append("dismiss"),
    )
    backend = SimpleNamespace(
        auto=SimpleNamespace(PatternId=SimpleNamespace(ExpandCollapsePattern=1)),
        invoke=lambda _: calls.append("invoke"),
    )
    secondary = SecondaryUI(SimpleNamespace(control_ui=ui, backend=backend))
    with pytest.raises(ControlUnavailable):
        secondary.member_submenu("12345", "member", "移出本群")
    assert calls == ["dismiss"]


def test_group_application_navigation_prefers_focused_keyboard_activation(monkeypatch):
    import quitbot_backend._engine.infrastructure.uia.secondary as module

    calls = []
    button = SimpleNamespace(IsKeyboardFocusable=True)
    menu = object()
    ui = SimpleNamespace(
        by_class=lambda _: menu, _dismiss_menu=lambda: calls.append("dismiss")
    )
    b = SimpleNamespace(
        auto=SimpleNamespace(SendKeys=lambda keys, **_: calls.append(keys)),
        focus_control=lambda x: calls.append("focus"),
    )
    s = SecondaryUI(SimpleNamespace(control_ui=ui, backend=b))
    s._app_button = lambda: button
    monkeypatch.setattr(module, "require_unlocked", lambda _: None)
    with s.apps(Target("12345", "测试群")) as actual:
        assert actual is menu
    assert calls == ["focus", "{Enter}", "dismiss"]


@pytest.mark.parametrize("draft_appears", [False, True])
def test_reaction_panel_cleanup_verifies_dismissal_and_preserves_new_drafts(
    monkeypatch, draft_appears
):
    import quitbot_backend._engine.infrastructure.uia.secondary as module

    calls = []
    visible = [False]
    panel = object()
    choice = SimpleNamespace(
        ClassName="stickers-list-item", IsOffscreen=False, GetPattern=lambda _: True
    )

    @contextmanager
    def checked(key):
        yield Target("12345", "测试群")

    def snapshot(control):
        if control == "editor":
            return (
                Node(children=(text("user draft"),))
                if draft_appears and visible[0]
                else Node()
            )
        return Node(children=(text("🙂"),))

    def invoke(control):
        calls.append("open-more")
        visible[0] = True

    def profile(*args):
        assert args == ("12345", "测试群", "group")
        calls.append("verified-profile")
        visible[0] = False

    ui = SimpleNamespace(
        checked=checked,
        message_menu=lambda *_: "menu",
        by_class=lambda _: panel if visible[0] else None,
        _dismiss_menu=lambda: calls.append("escape"),
    )
    b = SimpleNamespace(
        auto=SimpleNamespace(PatternId=SimpleNamespace(InvokePattern=1)),
        snapshot=snapshot,
        editor=lambda: "editor",
        walk=lambda node: [] if node == "menu" else [choice],
        invoke=invoke,
        profile=profile,
    )
    monkeypatch.setattr(module, "require_unlocked", lambda _: None)
    monkeypatch.setattr(module, "more_reaction_control", lambda *_: object())
    s = SecondaryUI(SimpleNamespace(control_ui=ui, backend=b))
    if draft_appears:
        with pytest.raises(DraftNotEmpty):
            s.reaction_palette("12345", "opaque")
        assert visible[0] and calls == ["open-more", "escape"]
    else:
        result = s.reaction_palette("12345", "opaque")
        assert not visible[0] and result.choices[0].protocol_id is None
        assert calls == ["open-more", "escape", "verified-profile"]


def test_file_actions_reject_search_suggestions_and_bad_ordinals_before_navigation():
    s = SecondaryUI(SimpleNamespace(control_ui=object(), backend=object()))
    for ordinal in (True, -1, "0", 1.5):
        with pytest.raises(ValueError):
            s.file_actions("12345", ordinal)
    with pytest.raises(ValueError):
        s.file_actions("12345", 0, query="name")


def test_onebot_secondary_actions_resolve_scoped_integer_ids_and_are_read_only():
    calls = []

    class Scope:
        def __init__(self, target):
            self.target = target
            self.id = target.id

        async def message_secondary(self, ui_id):
            calls.append(ui_id)
            return MessageMenuInfo((), ())

        async def files(self, **kwargs):
            return GroupFilesView(self.id, "群文件 - 测试群", (), None, None)

    group = Scope(Target("12345", "测试群"))
    other = Scope(Target("54321", "other"))
    private = Scope(Target("34567", "friend", "private"))
    qq = SimpleNamespace(
        account="bot",
        conversation=lambda key: {s.id: s for s in (group, other, private)}[key],
    )

    async def run():
        adapter = OneBotAdapter(qq, 23456)
        number = adapter.index.register(23456, group.target, "opaque")
        good = await adapter.handle(
            {
                "action": "x_get_message_secondary",
                "params": {"target_id": group.id, "message_id": number},
            }
        )
        assert good["retcode"] == 0 and good["data"] == {
            "actions": (),
            "reaction_controls": (),
        }
        bad = await adapter.handle(
            {
                "action": "x_get_message_secondary",
                "params": {"target_id": other.id, "message_id": number},
            }
        )
        assert bad["retcode"] == 1400 and calls == ["opaque"]
        bad = await adapter.handle(
            {"action": "x_get_group_files", "params": {"target_id": private.id}}
        )
        assert bad["retcode"] == 1400
        for action in (
            "set_group_reaction",
            "set_group_special_title",
            "rename_group_file",
        ):
            response = await adapter.handle({"action": action, "params": {}})
            assert response["retcode"] == 1404
        adapter.index.close()

    asyncio.run(run())
