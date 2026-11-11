"""Generate the transport contract without connecting to QQ."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import get_args, get_origin

from .app import create_app
from .auth import AccessKeys
from .config import AccountConfig, ConfigStore
from .contracts import ACTIONS
from .management import create_management_app
from .service import Service


def export(destination=Path("protocol")):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    access = AccessKeys()
    app = create_app(AccountConfig(account_id="10001"), access=access)
    with TemporaryDirectory() as temporary:
        service = Service(ConfigStore(Path(temporary) / "accounts.json"), access)
        management = create_management_app(service)
        (destination / "management.openapi.json").write_text(
            json.dumps(management.openapi(), ensure_ascii=False, indent=2) + "\n",
            "utf-8",
        )
    (destination / "openapi.json").write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    (destination / "actions.json").write_text(
        json.dumps(app.state.runtime.catalog, ensure_ascii=False, indent=2) + "\n",
        "utf-8",
    )
    matrix = destination.parent / "docs/capabilities.md"
    marker = "<!-- GENERATED_ACTIONS -->"
    if matrix.is_file() and marker in matrix.read_text("utf-8"):

        def label(kind):
            origin = get_origin(kind)
            if kind is type(None):
                return "null"
            if origin is list:
                return "list[" + label(get_args(kind)[0]) + "]"
            if origin is dict:
                return "JSON object"
            return getattr(kind, "__name__", str(kind))

        rows = ["| 动作 | 参数 | 返回 data | 写 |", "| --- | --- | --- | --- |"]
        for action in ACTIONS.values():
            params = (
                ", ".join(
                    name + ("" if field.is_required() else "?")
                    for name, field in action.params.model_fields.items()
                )
                or "无"
            )
            rows.append(
                f"| `{action.name}` | {params} | `{label(action.result)}` | {'是' if action.mutating else '否'} |"
            )
        prefix = matrix.read_text("utf-8").split(marker)[0]
        matrix.write_text(prefix + marker + "\n\n" + "\n".join(rows) + "\n", "utf-8")


if __name__ == "__main__":
    export()
