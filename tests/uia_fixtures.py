"""Load synthetic UIA trees shared by parser and adapter tests."""

import json
from pathlib import Path

from quitbot_backend._engine.models import Node


def load_tree(name: str) -> Node:
    def node(value: dict) -> Node:
        return Node(
            **{**value, "children": tuple(node(c) for c in value.get("children", ()))},
        )

    return node(
        json.loads((Path(__file__).parent / "fixtures" / name).read_text("utf-8"))
    )
