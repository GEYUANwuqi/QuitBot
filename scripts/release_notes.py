"""Collect one line per commit since the previous version tag."""

import argparse
import subprocess
from pathlib import Path


def release_notes(tag: str) -> str:
    def git(*args, check=True):
        return subprocess.run(
            ["git", *args],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    revision = git(
        "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"
    ).stdout.strip()
    previous = git(
        "describe",
        "--tags",
        "--match",
        "v[0-9]*",
        "--abbrev=0",
        f"{revision}^",
        check=False,
    )
    history = (
        f"{previous.stdout.strip()}..{revision}"
        if previous.returncode == 0
        else revision
    )
    messages = git("log", "--reverse", "--format=%B%x00", history).stdout
    lines = []
    for message in messages.split("\0"):
        subject, _, body = message.strip().partition("\n")
        description = " ".join((body.strip() or subject).split())
        if description:
            lines.append("- " + description)
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="Release tag, for example v0.15.2")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(release_notes(args.tag), encoding="utf-8")


if __name__ == "__main__":
    main()
