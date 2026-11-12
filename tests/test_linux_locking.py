import os
import subprocess
import sys

import pytest

from quitbot_backend._engine.locking import ui_transaction


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process-shared desktop lock")
def test_nested_desktop_transactions_exclude_other_processes(monkeypatch):
    import hashlib
    import tempfile

    display = f":{os.getpid()}"
    monkeypatch.setenv("DISPLAY", display + ".0")
    key = hashlib.sha256(display.encode()).hexdigest()[:16]
    path = os.path.join(
        tempfile.gettempdir(), f"quitbot-desktop-{os.getuid()}", key + ".lock"
    )
    probe = """
import fcntl,sys
with open(sys.argv[1], 'r+') as stream:
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(42)
"""

    def competing_process():
        return subprocess.run(
            [sys.executable, "-c", probe, path], timeout=5, check=False
        ).returncode

    with ui_transaction():
        with ui_transaction():
            assert competing_process() == 42
        assert competing_process() == 42
    assert competing_process() == 0
