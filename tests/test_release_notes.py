"""Exercise the release note CLI against isolated Git history."""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release_notes.py"


@pytest.fixture
def repository(tmp_path):
    def git(*args, message=None):
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            input=message,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout.strip()

    git("init", "--quiet")
    git("config", "user.name", "Fixture Author")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "commit.gpgsign", "false")
    git("config", "core.hooksPath", str(tmp_path / "empty-hooks"))
    return tmp_path, git


def generate(directory, tag):
    output = directory / "notes.md"
    subprocess.run(
        [sys.executable, str(SCRIPT), tag, "--output", str(output)],
        cwd=directory,
        capture_output=True,
        text=True,
        check=True,
    )
    return output.read_text("utf-8")


def test_body_is_one_line_with_subject_fallback_and_previous_tag_excluded(repository):
    directory, git = repository
    git(
        "commit",
        "--allow-empty",
        "--file=-",
        message="chore: 初始版本\n\n旧版本内容。\n",
    )
    git("tag", "-a", "v0.1.0", "-m", "Previous release")
    git(
        "commit",
        "--allow-empty",
        "--file=-",
        message="feat: 新增示例功能\n\n新增第一项。\n\n支持第二项。\n",
    )
    git(
        "commit", "--allow-empty", "--file=-", message="fix: 修复旧提交没有正文的情况\n"
    )
    git("tag", "-a", "v0.2.0", "-m", "Current release")
    assert generate(directory, "v0.2.0") == (
        "- 新增第一项。 支持第二项。\n- fix: 修复旧提交没有正文的情况\n"
    )


def test_first_release_collects_all_commits_in_order(repository):
    directory, git = repository
    git(
        "commit",
        "--allow-empty",
        "--file=-",
        message="feat: 初始功能\n\n实现初始功能。\n",
    )
    git(
        "commit",
        "--allow-empty",
        "--file=-",
        message="fix: 修复示例错误\n\n修复示例错误。\n",
    )
    git("tag", "v0.1.0")
    assert generate(directory, "v0.1.0") == "- 实现初始功能。\n- 修复示例错误。\n"


def test_unknown_tag_fails_without_creating_notes(repository):
    directory, _ = repository
    output = directory / "notes.md"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "v9.9.9", "--output", str(output)],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0 and not output.exists()
