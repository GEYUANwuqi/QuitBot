"""Require the prebuilt console in distributable wheels and source archives."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if version == "editable":
            return
        directory = Path(self.root) / "src/quitbot_backend/_webui"
        if (
            not (directory / "index.html").is_file()
            or not (directory / "THIRD_PARTY_LICENSES.txt").is_file()
            or not any((directory / "assets").glob("*.js"))
            or not any((directory / "assets").glob("*.css"))
        ):
            raise RuntimeError(
                "Console assets are missing. Run `npm --prefix frontend ci` and "
                "`npm --prefix frontend run build` before building a distribution."
            )
