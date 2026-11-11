import pytest

from quitbot_backend._engine.errors import TargetMismatch
from quitbot_backend._engine.uia import validate_group_code


def test_group_verification_requires_the_explicit_numeric_code_not_name_or_substring():
    assert validate_group_code(["群号", "20001"], "20001") == (
        "群号",
        "20001",
    )
    for values in (
        ["测试群"],
        ["200010"],
        ["20001", "20002"],
        ["群20001"],
    ):
        with pytest.raises(TargetMismatch):
            validate_group_code(values, "20001")
