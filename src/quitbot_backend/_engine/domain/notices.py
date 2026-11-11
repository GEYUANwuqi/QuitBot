"""Visible announcements, without invented server IDs or timestamps."""

from dataclasses import dataclass


@dataclass(frozen=True)
class GroupNotice:
    text: str
    author_name: str | None = None
    time_text: str | None = None
    id: None = None
    media_present: bool = False


@dataclass(frozen=True)
class GroupNotices:
    group_id: str
    items: tuple[GroupNotice, ...]
    scope: str = "loaded_visible_text"
    complete: bool = False


@dataclass(frozen=True)
class GroupNoticeMutationReceipt:
    group_id: str
    action: str
    before: GroupNotice
    after: GroupNotice | None
    verified_in_ui: bool = True


@dataclass(frozen=True)
class GroupNoticePinReceipt:
    group_id: str
    notice: GroupNotice
    before: bool
    after: bool
    verified_in_ui: bool = True
