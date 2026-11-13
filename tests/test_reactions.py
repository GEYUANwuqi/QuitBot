from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.errors import (
    ControlUnavailable,
    MutationOutcomeUnknown,
    TargetMismatch,
)
from quitbot_backend._engine.models import Message, Node, Target
from quitbot_backend._engine.ui.workflows.reactions import (
    ReactionsUI,
    parse_reaction_chip,
)


def chip(selected=False, count="1"):
    return Node(
        class_name="reaction-item" + (" reaction-item--clicked" if selected else ""),
        children=(
            Node(
                class_name="reaction-item-bg"
                + (" reaction-item-bg--clicked" if selected else "")
            ),
            Node(
                class_name="reaction-item-text",
                children=(Node("TextControl", count),),
            ),
        ),
    )


def test_reaction_chip_preserves_unknown_label_and_protocol_id():
    own = parse_reaction_chip(chip(True))
    peer = parse_reaction_chip(chip(False), label="赞")
    assert own.label is None and own.protocol_id is None
    assert own.count == peer.count == 1
    assert own.own_selected and not peer.own_selected
    assert peer.label == "赞"


@pytest.mark.parametrize("count", ["", "1万", "９"])
def test_count_is_not_invented_for_abbreviated_or_noninteger_text(count):
    assert parse_reaction_chip(chip(count=count)).count is None


def test_chip_rejects_missing_or_inconsistent_own_state():
    source = chip(True)
    with pytest.raises(ControlUnavailable):
        parse_reaction_chip(replace(source, children=source.children[1:]))
    with pytest.raises(ControlUnavailable):
        parse_reaction_chip(
            replace(source, children=(Node(class_name="reaction-item-bg"),))
        )
    with pytest.raises(ControlUnavailable):
        parse_reaction_chip(Node(class_name="more-reaction-item"))


def provider(monkeypatch, *, states=(), target_kind="group", cleanup_error=False):
    import quitbot_backend._engine.ui.workflows.reactions as module

    writes = []
    current = list(states)

    @contextmanager
    def checked(_):
        yield Target("12345", "测试群", target_kind)
        if cleanup_error:
            raise ControlUnavailable("header changed after operation")

    b = SimpleNamespace(editor=lambda: None, snapshot=lambda _: Node())
    ui = SimpleNamespace(
        checked=checked,
        message_menu=lambda *_: object(),
        _dismiss_menu=lambda: None,
    )
    client = SimpleNamespace(
        control_ui=ui,
        backend=b,
        get_message=lambda *_: Message("row", "12345", None, "fixture", "outgoing"),
    )
    p = ReactionsUI(client)
    p._states = lambda _: tuple(current)

    def submit(menu, label, submitted):
        submitted[0] = True
        writes.append(label)
        current.append(parse_reaction_chip(chip(True)))

    p._quick_submit = submit

    def wait_once(read, **_):
        value = read()
        if not value:
            raise ControlUnavailable("Postcondition did not hold")
        return value

    monkeypatch.setattr(module, "wait_until", wait_once)
    return p, writes, current, client


def test_add_reaction_validates_text_and_own_state_before_submission(monkeypatch):
    p, writes, current, _ = provider(monkeypatch)
    with pytest.raises(TargetMismatch):
        p.add_reaction("12345", "row", "赞", expected_text="different")
    current.append(parse_reaction_chip(chip(True)))
    with pytest.raises(ControlUnavailable):
        p.add_reaction("12345", "row", "爱心")
    assert not writes


@pytest.mark.parametrize("label", [0, ""])
def test_invalid_reaction_labels_fail_before_ui(monkeypatch, label):
    p, writes, _, _ = provider(monkeypatch)
    p.ui.checked = lambda _: pytest.fail("invalid label entered UI")
    with pytest.raises(ValueError):
        p.add_reaction("12345", "row", label)
    assert not writes


def test_add_reaction_accepts_other_users_chips_without_fabricating_label(monkeypatch):
    peer = parse_reaction_chip(chip(False, "2"))
    p, writes, _, _ = provider(monkeypatch, states=(peer,))
    receipt = p.add_reaction("12345", "row", "赞", expected_text="fixture")
    assert writes == ["赞"] and receipt.before is False and receipt.after is True
    assert receipt.count_before is None and receipt.label == "赞"
    read = p.reactions("12345", "row")
    assert all(item.label is None for item in read.items)


def test_add_postcondition_or_cleanup_failure_never_replays(monkeypatch):
    p, writes, _, _ = provider(monkeypatch, cleanup_error=True)
    with pytest.raises(MutationOutcomeUnknown):
        p.add_reaction("12345", "row", "赞")
    assert writes == ["赞"]
    p, writes, _, _ = provider(monkeypatch)

    def no_ui_change(menu, label, submitted):
        submitted[0] = True
        writes.append(label)

    p._quick_submit = no_ui_change
    with pytest.raises(MutationOutcomeUnknown):
        p.add_reaction("12345", "row", "赞")
    assert writes == ["赞"]


@pytest.mark.parametrize("own_count", [0, 1, 2])
def test_remove_only_unique_own_chip_and_preserves_peer_reactions(
    monkeypatch, own_count
):
    p, writes, current, _ = provider(monkeypatch)
    native = [chip(False, "2"), *[chip(True) for _ in range(own_count)]]

    def invoke(**_):
        writes.append("remove")
        current[:] = [parse_reaction_chip(native[0])]

    chips = [
        SimpleNamespace(
            IsEnabled=True, GetPattern=lambda _: SimpleNamespace(Invoke=invoke), node=n
        )
        for n in native
    ]
    current[:] = [parse_reaction_chip(n) for n in native]
    p._chips = lambda _: chips
    p.b.snapshot = lambda c: c.node if c is not None else Node()
    p.b.auto = SimpleNamespace(PatternId=SimpleNamespace(InvokePattern=1))
    if own_count > 1:
        with pytest.raises(ControlUnavailable):
            p.remove_own_reaction("12345", "row")
        assert not writes
    else:
        receipt = p.remove_own_reaction("12345", "row", expected_text="fixture")
        assert receipt.label is None and receipt.after is False
        assert receipt.before is bool(own_count)
        assert writes == (["remove"] if own_count else [])
        assert current[0].count == 2 and not current[0].own_selected


def test_quick_reaction_is_selected_by_fresh_tooltip_not_slot(monkeypatch):
    import quitbot_backend._engine.ui.workflows.reactions as module

    menu, window, more = object(), object(), object()
    current, writes = [None], []
    labels = ["", "新回应", "赞", "爱心", "汪汪", "敬礼"]
    items = [
        SimpleNamespace(
            ClassName="menu-stickers-item",
            IsEnabled=True,
            GetPattern=lambda _, name=name: SimpleNamespace(
                Invoke=lambda **__: writes.append(name)
            ),
        )
        for name in labels
    ]
    tips = SimpleNamespace(
        ClassName="sticker-list-tooltips-reaction", IsOffscreen=False
    )
    b = SimpleNamespace(
        window=window,
        auto=SimpleNamespace(
            ControlsAreSame=lambda a, c: a is c,
            PatternId=SimpleNamespace(InvokePattern=1),
        ),
        walk=lambda root: items if root is menu else [tips],
        snapshot=lambda _: Node(children=(Node("TextControl", current[0]),)),
    )

    @contextmanager
    def hover(_, item, **kwargs):
        assert kwargs == {"allow_clipped_reaction": True}
        current[0] = labels[items.index(item)]
        yield
        current[0] = None

    monkeypatch.setattr(module, "hover_control", hover)
    monkeypatch.setattr(module, "more_reaction_control", lambda *_: more)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    def wait_once(read, predicate=bool, **_):
        value = read()
        if not predicate(value):
            raise ControlUnavailable("No accessible tooltip")
        return value

    monkeypatch.setattr(module, "wait_until", wait_once)
    p = ReactionsUI(SimpleNamespace(control_ui=None, backend=b))
    submitted = [False]
    p._quick_submit(menu, "赞", submitted)
    assert writes == ["赞"] and submitted == [True]


def test_palette_reaction_rebinds_after_scroll_and_rejects_missing_or_ambiguous_text(monkeypatch):
    import quitbot_backend._engine.ui.workflows.reactions as module

    menu = object()
    more = SimpleNamespace(ClassName="menu-stickers-item")
    monkeypatch.setattr(module, "more_reaction_control", lambda *_: more)

    def wait_once(read, predicate=bool, **_):
        value = read()
        if not predicate(value):
            raise ControlUnavailable("Requested text is not loaded")
        return value

    monkeypatch.setattr(module, "wait_until", wait_once)
    for outcome in ("unique", "recent_duplicate", "duplicate_after_scroll", "missing"):
        writes = []
        live = []
        fresh = SimpleNamespace(
            ClassName="stickers-list-item", IsOffscreen=False, IsEnabled=True,
            text="🎉", GetPattern=lambda _, writes=writes: SimpleNamespace(
                Invoke=lambda **_: writes.append("fresh")
            ),
        )

        def scroll(live=live, fresh=fresh, outcome=outcome, **_):
            live[:] = [fresh, fresh] if outcome == "duplicate_after_scroll" else [fresh]

        old = SimpleNamespace(
            ClassName="stickers-list-item", IsOffscreen=True, IsEnabled=True,
            text="🎊" if outcome == "missing" else "🎉",
            GetPattern=lambda _, scroll=scroll: SimpleNamespace(ScrollIntoView=scroll),
        )
        live[:] = [old]
        recent = SimpleNamespace(ClassName="title", text="最近表情")
        canonical = SimpleNamespace(ClassName="title", text="emoji 表情")
        panel = SimpleNamespace(GetChildren=lambda live=live, fresh=fresh, outcome=outcome,
                                recent=recent, canonical=canonical: (
            [recent, fresh, canonical, *live] if outcome == "recent_duplicate"
            else [canonical, *live]
        ))
        backend = SimpleNamespace(
            auto=SimpleNamespace(PatternId=SimpleNamespace(ScrollItemPattern=1, InvokePattern=2)),
            walk=lambda root, live=live: [more] if root is menu else live,
            snapshot=lambda c: Node(children=(Node("TextControl", c.text),)),
            invoke=lambda c: None if c is more else pytest.fail("Unexpected navigation"),
        )
        ui = SimpleNamespace(by_class=lambda _, panel=panel: panel)
        port = ReactionsUI(SimpleNamespace(control_ui=ui, backend=backend))
        submitted = [False]
        if outcome in ("unique", "recent_duplicate"):
            port._palette_submit(menu, "🎉", submitted)
            assert writes == ["fresh"] and submitted == [True]
        else:
            with pytest.raises(ControlUnavailable):
                port._palette_submit(menu, "🎉", submitted)
            assert not writes and submitted == [False]
