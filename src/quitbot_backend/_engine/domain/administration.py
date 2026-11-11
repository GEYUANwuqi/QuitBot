"""Receipts for native group administrator changes."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MemberAdminReceipt:
    group_id: str
    member_name: str
    user_id: str
    before: bool
    after: bool
    identity_source: str = "fresh_uia_profile"
    verified_in_ui: bool = True
