"""Normalize AT-SPI message trees for the shared message parser."""

from collections import defaultdict

from ...models import Node

ROLE_TYPES = {
    "static": "TextControl",
    "image": "ImageControl",
    "push button": "ButtonControl",
    "toggle button": "ButtonControl",
    "section": "GroupControl",
    "panel": "GroupControl",
    "paragraph": "GroupControl",
    "entry": "EditControl",
    "frame": "WindowControl",
}


def normalize_chat(snapshot, header, frame_path=None):
    """Require one named chat and preserve DOM IDs; never dedupe by text.

    This operates on evidence, not a live window binding. AT-SPI can expose
    preloaded/unmapped windows, so live interactions also need X11 validation.
    """
    if snapshot.get("truncated") or snapshot.get("errors"):
        raise ValueError("Incomplete snapshot: inspect truncation/errors first")
    rows = snapshot["nodes"]
    matches = [
        row
        for row in rows
        if "chat-header__contact-name" in row["attributes"].get("class", "").split()
        and row["name"] == header
        and (frame_path is None or tuple(row["path"][:2]) == tuple(frame_path))
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one chat header {header!r}, found {len(matches)}")
    prefix = tuple(matches[0]["path"][:2])
    selected = [row for row in rows if tuple(row["path"][:2]) == prefix]
    if any(row.get("text_truncated") for row in selected):
        raise ValueError("Chat contains truncated text")
    by_path = {tuple(row["path"]): row for row in selected}
    if len(by_path) != len(selected):
        raise ValueError("Duplicate snapshot paths")
    children = defaultdict(list)
    for path, row in by_path.items():
        if path != prefix:
            if path[:-1] not in by_path:
                raise ValueError("Missing parent in snapshot")
            children[path[:-1]].append(path)
        if "ml-item" in row["attributes"].get("class", "").split() and not row[
            "attributes"
        ].get("id"):
            raise ValueError("Message row has no DOM id; cannot use ID cursor")

    def build(path):
        row = by_path[path]
        attributes = row["attributes"]
        return Node(
            type=ROLE_TYPES.get(row["role"], row["role"]),
            name=row["name"],
            class_name=attributes.get("class", ""),
            automation_id=attributes.get("id", ""),
            children=tuple(build(p) for p in sorted(children[path])),
        )

    return build(prefix)
