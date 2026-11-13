from types import SimpleNamespace

import pytest

from quitbot_backend._engine.errors import ControlUnavailable, TargetMismatch
from quitbot_backend._engine.ui.workflows import profile_cards as module


def lab(monkeypatch):
    fields = {1: ["QQ", "11111"]}
    cards = [SimpleNamespace(NativeWindowHandle=1)]
    visible = [False]
    backend = SimpleNamespace(
        window=SimpleNamespace(ProcessId=10),
        auto=SimpleNamespace(GetRootControl=lambda: SimpleNamespace(GetChildren=list)),
        walk=lambda card: [
            SimpleNamespace(ControlTypeName="TextControl", Name=n)
            for n in fields[card.NativeWindowHandle]
        ],
    )
    monkeypatch.setattr(
        module, "native_cards", lambda auto, pid: [(c, visible[0]) for c in cards]
    )
    return backend, fields, cards, visible


def test_hidden_cached_identity_cannot_validate_a_new_opening(monkeypatch):
    b, fields, _, _ = lab(monkeypatch)
    probe = module.ProfileCards(b)
    assert probe.refreshed(lambda xs: "11111" in xs) is None
    fields[1] = ["QQ", "22222"]
    assert probe.refreshed(lambda xs: "11111" in xs) is None
    assert probe.refreshed(lambda xs: "22222" in xs)[2] == ("QQ", "22222")


def test_existing_visible_profile_is_never_reused_or_closed(monkeypatch):
    b, _, _, visible = lab(monkeypatch)
    visible[0] = True
    with pytest.raises(ControlUnavailable):
        module.ProfileCards(b)


def test_multiple_refreshed_cards_fail_closed(monkeypatch):
    b, fields, cards, _ = lab(monkeypatch)
    probe = module.ProfileCards(b)
    cards.append(SimpleNamespace(NativeWindowHandle=2))
    fields[1] = fields[2] = ["QQ", "22222"]
    with pytest.raises(TargetMismatch):
        probe.refreshed(lambda xs: "22222" in xs)
