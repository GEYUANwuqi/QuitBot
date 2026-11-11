"""Receipts for member interactions verified against fresh native profiles."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MemberCardReceipt:
    group_id: str
    user_id: str
    before: str
    after: str
    before_name: str
    after_name: str
    nickname: str | None = None
    identity_source: str = "fresh_uia_profile"
    verified_in_ui: bool = True


@dataclass(frozen=True)
class MemberPokeReceipt:
    group_id: str
    user_id: str
    member_name: str
    ui_message_id: str
    system_text: str
    identity_source: str = "fresh_uia_profile"
    verified_in_ui: bool = True
