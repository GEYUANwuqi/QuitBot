import pytest

from quitbot_backend._engine.uia import UIABackend, wait_until


class StaleElement(Exception):
    hresult = -2147220991


def test_wait_retries_only_transient_provider_reads(monkeypatch):
    calls = []

    def read():
        calls.append(1)
        if len(calls) == 1:
            raise StaleElement()
        return "ready"

    monkeypatch.setattr("quitbot_backend._engine.uia.time.sleep", lambda seconds: None)
    assert wait_until(read) == "ready"
    assert len(calls) == 2
    with pytest.raises(ValueError):
        wait_until(lambda: (_ for _ in ()).throw(ValueError("real error")))


def test_find_restarts_from_root_instead_of_returning_partial_data(monkeypatch):
    backend = UIABackend.__new__(UIABackend)
    attempts = []

    def walk(root):
        attempts.append(root)
        if len(attempts) == 1:
            raise StaleElement()
        yield "found"

    backend.walk = walk
    monkeypatch.setattr("quitbot_backend._engine.uia.time.sleep", lambda seconds: None)
    assert backend.find("root", lambda c: c == "found") == "found"
    assert attempts == ["root", "root"]
