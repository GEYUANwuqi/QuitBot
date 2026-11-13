from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter

from quitbot_backend._engine.domain.stickers import FavoriteSticker
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    InstanceClosed,
    MutationOutcomeUnknown,
    TargetMismatch,
)
from quitbot_backend._engine.instance import FavoriteStickerCollection
from quitbot_backend._engine.ui.workflows.stickers import (
    StickersUI,
    select_sticker,
)


def test_sticker_reference_preserves_native_platform_identity_in_api_results():
    from quitbot_backend.contracts import ACTIONS, validate_result

    for identity in ((1, 2, 3), (":1.42", "/org/a11y/atspi/accessible/7")):
        sticker = FavoriteSticker("test", 1, identity, "fixture", 0)
        result = validate_result(ACTIONS["x_get_favorite_stickers"], [sticker])
        assert result[0]["runtime_id"] == list(identity)
        assert (
            TypeAdapter(FavoriteSticker).validate_python(result[0]).runtime_id
            == identity
        )


def test_sticker_identity_rejects_cross_account_stale_and_ambiguous_names():
    first = FavoriteSticker("test", 10, (1, 2), "自定义表情", 0)
    second = replace(first, runtime_id=(1, 3), index=1)
    assert select_sticker((first, second), second, "test", 10) is second
    with pytest.raises(TargetMismatch):
        select_sticker((first,), first, "another", 10)
    with pytest.raises(ControlUnavailable):
        select_sticker((first,), second, "test", 10)
    with pytest.raises(ValueError):
        select_sticker((first,), "自定义表情", "test", 10)
    named = replace(second, name="唯一备注")
    assert select_sticker((first, named), "唯一备注", "test", 10) is named
    with pytest.raises(ControlUnavailable):
        select_sticker((replace(first, name=named.name), named), named.name, "test", 10)
    conversation = SimpleNamespace(target="peer", _client=lambda: None)
    scope = FavoriteStickerCollection(
        conversation, SimpleNamespace(list=lambda key: (first, second))
    )
    assert scope.items() == (first, second)
    scope._active = False
    with pytest.raises(InstanceClosed):
        scope.items()


@pytest.mark.parametrize("by_name", [False, True])
@pytest.mark.parametrize("other_name_changed", [False, True])
def test_sticker_delete_rechecks_the_callers_identity_and_never_replays(
    monkeypatch,
    by_name,
    other_name_changed,
):
    source = FavoriteSticker("test", 10, (1, 2), "test", 0)
    retained = replace(source, runtime_id=(1, 3), name="retained", index=1)
    unexpected = replace(
        retained,
        runtime_id=(1, 4),
        name="different" if other_name_changed else retained.name,
    )
    calls = []
    api = StickersUI.__new__(StickersUI)
    api.b = SimpleNamespace(invoke=lambda action: calls.append(action))
    api._select = lambda _: (object(), source)
    api._menu_action = lambda *args: "delete"
    readings = iter(((source, retained), (unexpected,)))
    api._items = lambda: next(readings)

    @contextmanager
    def panel(_):
        yield
        calls.append("closed")

    api.panel = panel
    monkeypatch.setattr(
        "quitbot_backend._engine.ui.workflows.stickers.wait_until",
        lambda read, predicate=bool, **_: read(),
    )
    selector = source.name if by_name else source
    if by_name and not other_name_changed:
        assert api.delete("peer", selector) == source
    else:
        with pytest.raises(MutationOutcomeUnknown):
            api.delete("peer", selector)
    assert calls.count("delete") == 1


@pytest.mark.parametrize("normalized", [False, True])
def test_remark_waits_for_qq_normalization_before_submitting(normalized):
    source = FavoriteSticker("test", 10, (1, 2), "old", 0)
    calls = []
    field = SimpleNamespace(Value="old")
    field.GetValuePattern = lambda: field
    field.SetValue = lambda value, **_: setattr(field, "Value", value)

    class Confirm:
        reads = 0

        @property
        def IsEnabled(self):
            self.reads += 1
            if self.reads == 1:
                return False
            if normalized:
                field.Value = "newremark"
            return True

    confirm = Confirm()
    api = StickersUI.__new__(StickersUI)
    api.b = SimpleNamespace(
        invoke=lambda control: calls.append(control),
        focus_control=lambda _: None,
        ensure_focus=lambda _: None,
        auto=SimpleNamespace(SendKeys=lambda *a, **kw: None),
    )
    api.ui = SimpleNamespace(by_class=lambda _: None)
    api._items = lambda: (replace(source, name=field.Value),)
    api._select = lambda _: (object(), api._items()[0])
    api._button = lambda *a: confirm

    @contextmanager
    def panel(_):
        yield

    @contextmanager
    def editor(_):
        yield object(), field

    api.panel = panel
    api._remark_editor = editor
    if normalized:
        with pytest.raises(ControlUnavailable, match="normalized"):
            api.remark("peer", "old", "new-remark")
        assert calls == []
    else:
        assert api.remark("peer", "old", "new-remark") == "new-remark"
        assert calls == [confirm]
    assert confirm.reads >= 2
