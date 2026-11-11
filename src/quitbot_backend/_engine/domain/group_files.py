"""UI-backed group file identities; QQ server IDs are deliberately unavailable."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class GroupFileEntry:
    name: str
    kind: Literal["file", "folder", "unknown"]
    size_text: str | None = None
    count: int | None = None
    uploader_text: str | None = None
    date_text: str | None = None
    usage_text: str | None = None
    id: None = None


@dataclass(frozen=True)
class GroupFileListing:
    group_id: str
    items: tuple[GroupFileEntry, ...]
    scope: str = "loaded_root"
    complete: bool = False
    folder: str | None = None


@dataclass(frozen=True)
class GroupFileMutationReceipt:
    group_id: str
    action: str
    name: str
    before: GroupFileEntry | None
    after: GroupFileEntry | None
    verified_in_ui: bool = True
    scope: str = "loaded_root"
    local_path: str | None = None
    source_folder: str | None = None
    folder: str | None = None


@dataclass(frozen=True)
class GroupFileDownloadReceipt:
    group_id: str
    name: str
    local_path: str
    size_bytes: int
    sha256: str
    verified_in_ui: bool = True
