"""UI-observed group admission settings and their complete change receipts."""

from dataclasses import dataclass


@dataclass(frozen=True)
class GroupJoinPolicy:
    group_id: str
    join_mode: str
    verification: str | None
    member_invites: bool | None
    invite_approval: str | None
    bot_invites: bool | None
    bot_invite_approval: str | None


@dataclass(frozen=True)
class GroupJoinPolicyReceipt:
    group_id: str
    setting: str
    before: GroupJoinPolicy
    after: GroupJoinPolicy
    verified_in_ui: bool = True


@dataclass(frozen=True)
class GroupTagReceipt:
    group_id: str
    label: str
    before: tuple[str, ...]
    after: tuple[str, ...]
    verified_in_ui: bool = True
