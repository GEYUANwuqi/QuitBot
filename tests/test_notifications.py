"""Notification visits must release the main page and keep chat bindings."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.errors import ControlUnavailable, MutationOutcomeUnknown
from quitbot_backend._engine.models import Node
from quitbot_backend._engine.ui.workflows import notifications
from quitbot_backend._engine.ui.workflows.notifications import NotificationUI


@pytest.fixture
def notification_ui(monkeypatch):
    state = SimpleNamespace(page="消息", calls=[], failure=None, cleanup_failure=False)
    main, detached = object(), object()
    backend = SimpleNamespace(
        window=detached,
        main_window=main,
        auto=object(),
        check_thread=lambda: None,
        _require_no_modal=lambda: None,
    )
    backend.use_main_window = lambda: setattr(backend, "window", main)

    def navigate(name):
        backend.use_main_window()
        state.calls.append(name)
        if name == "消息" and state.cleanup_failure:
            raise ControlUnavailable("message page unavailable")
        state.page = name

    backend.navigate = navigate
    ui = NotificationUI(SimpleNamespace(backend=backend))

    def page(kind):
        backend.navigate("联系人")
        if state.failure == "page":
            raise ControlUnavailable("notification page unavailable")
        return object()

    def snapshot(_):
        if state.failure == "snapshot":
            raise ControlUnavailable("notification tree rebuilt")
        return Node()

    ui.page, backend.snapshot = page, snapshot
    monkeypatch.setattr(notifications, "ui_transaction", nullcontext)
    monkeypatch.setattr(notifications, "require_unlocked", lambda _: None)
    return ui, state, detached


@pytest.mark.parametrize("kind", ["friend", "group"])
@pytest.mark.parametrize("failure", [None, "page", "snapshot"])
def test_notification_read_restores_message_page_and_detached_window(
    notification_ui, kind, failure
):
    ui, state, previous = notification_ui
    state.failure = failure
    if failure:
        with pytest.raises(ControlUnavailable, match="notification"):
            ui.read(kind)
    else:
        assert ui.read(kind) == []
    assert state.page == "消息"
    assert state.calls == ["联系人", "消息"]
    assert ui.b.window is previous


def test_notification_navigation_keeps_existing_modal_and_window(notification_ui):
    ui, state, previous = notification_ui

    def pending():
        raise ControlUnavailable("existing QQ dialog")

    ui.b._require_no_modal = pending
    with pytest.raises(ControlUnavailable, match="existing QQ dialog"):
        ui.read("friend")
    assert state.calls == [] and ui.b.window is previous


@pytest.mark.parametrize(
    "kind,approve", [("group", True), ("group", False), ("friend", False)]
)
@pytest.mark.parametrize(
    "failure", [None, "invoke", "verify", "cleanup", "verify_cleanup", "stale"]
)
def test_request_decision_cleans_up_once_without_replaying_write(
    notification_ui, monkeypatch, kind, approve, failure
):
    ui, state, previous = notification_ui
    row = SimpleNamespace(ClassName="list-item")
    leaf = SimpleNamespace(
        IsEnabled=True,
        Name="同意" if approve else "拒绝",
        ClassName="",
        ControlTypeName="MenuItemControl"
        if kind == "friend" and not approve
        else "ButtonControl",
    )
    arrow = SimpleNamespace(
        ClassName="q-select-button-action__wrapper",
        ControlTypeName="GroupControl",
        Name="",
    )
    menu = SimpleNamespace(
        ClassName="q-select-button-options", ControlTypeName="MenuControl"
    )
    card = SimpleNamespace(
        ui_key="uia:fixture", status="pending", actions=("同意", "拒绝")
    )
    calls = []
    menu_calls = []
    ui.b.walk = lambda _: [row]

    def find(root, predicate, required=True):
        controls = (
            [arrow, leaf]
            if root is row
            else [menu]
            if root is ui.b.main_window
            else [leaf]
        )
        return next(c for c in controls if predicate(c))

    ui.b.find = find
    ui.b.snapshot = lambda _: Node()
    state.cleanup_failure = failure in ("cleanup", "verify_cleanup")

    def cards(*_):
        if not calls:
            return [] if failure == "stale" else [card]
        if failure in ("verify", "verify_cleanup"):
            raise ControlUnavailable("request verification unavailable")
        return [
            SimpleNamespace(
                ui_key=card.ui_key, status="已同意" if approve else "已拒绝"
            )
        ]

    def invoke(control):
        if control is arrow:
            menu_calls.append("open")
            return
        assert control is leaf and state.page == "联系人"
        calls.append("submit")
        if failure == "invoke":
            raise ControlUnavailable("provider lost after submit")

    ui.b.invoke = invoke
    monkeypatch.setattr(notifications, "parse_system_cards", cards)
    if failure == "stale":
        with pytest.raises(ControlUnavailable, match="stale"):
            ui.respond(kind, card.ui_key, approve)
        assert calls == []
    elif failure:
        with pytest.raises(MutationOutcomeUnknown):
            ui.respond(kind, card.ui_key, approve)
        assert calls == ["submit"]
    else:
        receipt = ui.respond(kind, card.ui_key, approve)
        assert receipt["verified_in_ui"] and receipt["approve"] is approve
        assert calls == ["submit"]
    assert ui.b.window is previous
    assert state.calls.count("消息") == 1
    assert menu_calls == (["open"] if kind == "friend" and failure != "stale" else [])
    if not state.cleanup_failure:
        assert state.page == "消息"


def test_cleanup_keeps_original_read_error_and_restores_window(notification_ui):
    ui, state, previous = notification_ui
    state.failure, state.cleanup_failure = "snapshot", True
    with pytest.raises(ControlUnavailable, match="notification tree rebuilt") as error:
        ui.read("friend")
    assert ui.b.window is previous
    assert any("message page unavailable" in note for note in error.value.__notes__)
