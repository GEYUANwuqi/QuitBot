import pytest

from quitbot_backend._engine.client import QQClient
from quitbot_backend._engine.errors import TargetMismatch
from quitbot_backend._engine.models import Target


def configured_client():
    client = QQClient.__new__(QQClient)
    client.targets = (Target("1", "测试"), Target("2", "测试群"))
    return client


@pytest.mark.parametrize("text", ["   ", "abc\x00", "x" * 3001])
def test_invalid_text_is_rejected_before_touching_desktop(text):
    with pytest.raises(ValueError):
        configured_client().send_text("1", text)


def test_unconfigured_targets_cannot_trigger_ui_input():
    with pytest.raises(TargetMismatch):
        configured_client().send_text("not-configured", "test")


def test_duplicate_names_require_unambiguous_numeric_id():
    client = configured_client()
    client.targets += (Target("3", "测试"),)
    with pytest.raises(TargetMismatch):
        client.target("测试")
    assert client.target("3").id == "3"
