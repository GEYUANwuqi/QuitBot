import pytest

from quitbot_backend._engine.errors import ControlUnavailable, TargetMismatch
from quitbot_backend._engine.models import Target
from quitbot_backend._engine.uia import UIABackend


def make_backend(error):
    b = UIABackend.__new__(UIABackend)
    b.auto = object()
    b.window = object()
    b.walk = lambda _: []
    b.open_session = lambda name: (_ for _ in ()).throw(error)
    opened = []
    b.open_from_contacts = opened.append
    return b, opened


def test_missing_recent_session_falls_back_to_contacts():
    b, opened = make_backend(ControlUnavailable("not loaded"))
    target = Target("20001", "测试群")
    b.open_target(target)
    assert opened == [target]


def test_ambiguous_recent_session_is_never_hidden_by_fallback():
    b, opened = make_backend(TargetMismatch("duplicate names"))
    with pytest.raises(TargetMismatch):
        b.open_target(Target("20001", "测试群"))
    assert opened == []


def test_explicit_recent_route_does_not_fall_back():
    b, opened = make_backend(ControlUnavailable("not loaded"))
    with pytest.raises(ControlUnavailable):
        b.open_target(Target("20001", "测试群"), route="recent")
    assert opened == []
