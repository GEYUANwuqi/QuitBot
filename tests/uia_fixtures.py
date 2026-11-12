"""Load synthetic UIA trees shared by parser and adapter tests."""

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from quitbot_backend._engine import native
from quitbot_backend._engine.models import Node


def mock_context_menu_interface(monkeypatch):
    """Supply the COM interface marker used by simulated QueryInterface calls."""
    monkeypatch.setattr(native, "sys", SimpleNamespace(platform="win32"))
    module = ModuleType("comtypes.gen.UIAutomationClient")
    module.IUIAutomationElement3 = object()
    monkeypatch.setitem(sys.modules, module.__name__, module)


def load_tree(name: str) -> Node:
    def node(value: dict) -> Node:
        return Node(
            **{**value, "children": tuple(node(c) for c in value.get("children", ()))},
        )

    return node(
        json.loads((Path(__file__).parent / "fixtures" / name).read_text("utf-8"))
    )
