from dataclasses import dataclass


@dataclass(frozen=True)
class ControlInfo:
    key: str
    role: str
    name: str
    class_name: str
    automation_id: str
    enabled: bool
    focusable: bool
    patterns: tuple[str, ...]
    risk: str
    label: str | None = None


@dataclass(frozen=True)
class MenuAction:
    name: str
    enabled: bool
    risk: str


@dataclass(frozen=True)
class ActionPlan:
    action: str
    target_id: str
    exposed: bool
    enabled: bool
    committable: bool
    reason: str


@dataclass(frozen=True)
class DraftState:
    text: str
    has_quote: bool
    has_media: bool
    mentions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChatSettings:
    target_id: str
    pinned: bool | None
    muted: bool | None
    blocked: bool | None = None
    own_card: str | None = None
    remark: str | None = None


@dataclass(frozen=True)
class GroupPreferences:
    group_id: str
    whole_banned: bool | None
    speak_limit: int | None
    message_mode: str | None


@dataclass(frozen=True)
class MessageMutationReceipt:
    target_id: str
    message_id: str
    action: str
    before: str | bool | None
    after: str | bool | None
    notice_text: str | None = None
    verified_in_ui: bool = True


@dataclass(frozen=True)
class MemberProfile:
    group_id: str
    name: str
    user_id: str
    role: str
    fields: tuple[str, ...]
    nickname: str | None = None


@dataclass(frozen=True)
class SettingReceipt:
    target_id: str
    setting: str
    before: str | bool | None
    after: str | bool | None
    verified_in_ui: bool = True


@dataclass(frozen=True)
class ForwardReceipt:
    source_id: str
    source_message_id: str
    target_id: str
    message_id: str
    verified_in_ui: bool = True


@dataclass(frozen=True)
class SubmenuInfo:
    entry: str
    mechanism: str
    actions: tuple[MenuAction, ...]


@dataclass(frozen=True)
class GroupFileItem:
    row_index: int
    name: str
    size_text: str | None
    usage_text: str | None
    date_text: str | None
    uploader_text: str | None
    id: str | None = None


@dataclass(frozen=True)
class GroupFilesView:
    group_id: str
    title: str
    items: tuple[GroupFileItem, ...]
    total_text: str | None
    capacity_text: str | None
    complete: bool = False
    scope: str = "loaded_root"
    query: str = ""
    order: str | None = None


@dataclass(frozen=True)
class FileSortChoice:
    key: str
    label: str
    selected: bool | None
    enabled: bool


@dataclass(frozen=True)
class FileSortState:
    group_id: str
    current: str | None
    choices: tuple[FileSortChoice, ...]


@dataclass(frozen=True)
class ReactionChoice:
    row_index: int
    label: str | None
    kind: str
    invokable: bool
    offscreen: bool
    protocol_id: str | None = None


@dataclass(frozen=True)
class ReactionPanel:
    message_id: str
    choices: tuple[ReactionChoice, ...]
    submission_tested: bool = False


@dataclass(frozen=True)
class ReactionControlInfo:
    role: str
    name: str
    class_name: str
    invokable: bool


@dataclass(frozen=True)
class MessageMenuInfo:
    actions: tuple[MenuAction, ...]
    reaction_controls: tuple[ReactionControlInfo, ...]


@dataclass(frozen=True)
class MemberBanReceipt:
    group_id: str
    member_name: str
    requested_duration: int
    before: bool
    after: bool
    user_id: str | None = None
    identity_source: str = "unique_loaded_display_name"
    verified_in_ui: bool = True


@dataclass(frozen=True)
class MemberRemovalReceipt:
    group_id: str
    member_name: str
    user_id: str
    absent_in_loaded_roster: bool
    verified_in_ui: bool = True


@dataclass(frozen=True)
class GroupLeaveReceipt:
    group_id: str
    confirmation_closed: bool
    membership_verified: bool = False
    verified_in_ui: bool = False
