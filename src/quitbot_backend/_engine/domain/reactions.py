"""UI-observed message reactions, without invented protocol emoji identifiers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ReactionChip:
    label: str | None
    count: int | None
    own_selected: bool
    protocol_id: str | None = None


@dataclass(frozen=True)
class MessageReactions:
    target_id: str
    message_id: str
    items: tuple[ReactionChip, ...]
    complete: bool = False


@dataclass(frozen=True)
class ReactionReceipt:
    target_id: str
    message_id: str
    label: str | None
    before: bool
    after: bool
    count_before: int | None
    count_after: int | None
    action: str = "add"
    verified_in_ui: bool = True
    protocol_id: str | None = None
