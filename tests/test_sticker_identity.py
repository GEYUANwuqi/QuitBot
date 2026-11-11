from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.domain.stickers import FavoriteSticker
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    InstanceClosed,
    MutationOutcomeUnknown,
    TargetMismatch,
)
from quitbot_backend._engine.infrastructure.uia.stickers import (
    StickersUI,
    select_sticker,
)
from quitbot_backend._engine.instance import FavoriteStickerCollection


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


def test_sticker_delete_reports_unknown_instead_of_replaying_when_other_item_changes(
    monkeypatch,
):
    source = FavoriteSticker("test", 10, (1, 2), "test", 0)
    retained = replace(source, runtime_id=(1, 3), name="retained", index=1)
    unexpected = replace(retained, runtime_id=(1, 4))
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
        "quitbot_backend._engine.infrastructure.uia.stickers.wait_until",
        lambda read, predicate=bool, **_: read(),
    )
    with pytest.raises(MutationOutcomeUnknown):
        api.delete("peer", source)
    assert calls.count("delete") == 1
