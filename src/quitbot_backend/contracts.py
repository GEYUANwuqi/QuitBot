"""One action catalog for validation, HTTP/OpenAPI, WS and documentation."""

from dataclasses import dataclass
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    TypeAdapter,
    create_model,
)

from ._engine.domain import controls as c
from ._engine.domain.activities import SystemCard
from ._engine.domain.administration import MemberAdminReceipt
from ._engine.domain.essence import GroupEssences
from ._engine.domain.group_files import (
    GroupFileDownloadReceipt,
    GroupFileEntry,
    GroupFileListing,
    GroupFileMutationReceipt,
)
from ._engine.domain.group_settings import (
    GroupJoinPolicy,
    GroupJoinPolicyReceipt,
    GroupTagReceipt,
)
from ._engine.domain.member_interactions import MemberCardReceipt, MemberPokeReceipt
from ._engine.domain.notices import (
    GroupNotice,
    GroupNoticeMutationReceipt,
    GroupNoticePinReceipt,
    GroupNotices,
)
from ._engine.domain.reactions import MessageReactions, ReactionReceipt
from ._engine.domain.stickers import FavoriteSticker, StickerImportReceipt
from ._engine.models import (
    ConversationCandidate,
    ConversationInfo,
    GroupMemberInfo,
    HistoryRecord,
    InstanceStatus,
    Message,
    Target,
)

Identifier = (
    Annotated[str, Field(pattern=r"^[1-9][0-9]*$")] | Annotated[StrictInt, Field(gt=0)]
)
Text = Annotated[str, Field(min_length=1)]
JsonObject = dict[str, JsonValue]
T = TypeVar("T")


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UnknownOutcome(BaseModel):
    retryable: Literal[False]
    outcome: Literal["unknown"]


class Reply(BaseModel, Generic[T]):  # noqa: UP046 - Pydantic generic model compatibility
    status: Literal["ok", "failed"]
    retcode: int
    data: T | UnknownOutcome | None = None
    echo: JsonValue = None
    message: str | None = None
    wording: str | None = None


class MessageId(BaseModel):
    model_config = ConfigDict(extra="allow")
    message_id: int


class ObservedMessage(BaseModel):
    message_id: int
    message: Message


class LoginInfo(BaseModel):
    user_id: int
    nickname: str


class VersionInfo(BaseModel):
    model_config = ConfigDict(extra="allow")
    app_name: str
    app_version: str
    protocol_version: str


class ClientVersion(BaseModel):
    client_name: str
    client_version: str
    source: str


class StoragePaths(BaseModel):
    download_directory: str
    data_directory: str | None = None


class ProtocolSegment(BaseModel):
    type: str
    data: JsonObject


class Sender(BaseModel):
    user_id: int
    nickname: str


class ProtocolMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    time: int
    message_type: Literal["group", "private"]
    message_id: int
    sender: Sender
    message: list[ProtocolSegment]


class RequestDecision(BaseModel):
    ui_key: str
    kind: Literal["friend", "group"]
    approve: bool
    status: str
    verified_in_ui: bool


class ActionInfo(BaseModel):
    name: str
    summary: str
    mutating: bool
    added: bool
    params: JsonObject
    response: JsonObject


class ApiCatalog(BaseModel):
    version: str
    actions: list[ActionInfo]
    schemas: dict[str, JsonObject]


class CapabilitySummary(BaseModel):
    actions: list[ActionInfo]
    transport: Literal["UIA", "AT-SPI"]
    partial: bool
    scope: str


class GroupInfo(BaseModel):
    model_config = ConfigDict(extra="allow")
    group_id: int
    group_name: str


class MemberInfo(BaseModel):
    model_config = ConfigDict(extra="allow")
    group_id: int
    user_id: int
    nickname: str
    card: str
    role: str


class Enabled(BaseModel):
    enabled: bool


class Pinned(BaseModel):
    pinned: bool


class FolderProperties(BaseModel):
    group_id: str
    entry: GroupFileEntry
    fields: list[str]


class SavedQR(BaseModel):
    group_id: str
    path: str
    size_bytes: int
    sha256: str


class ProfileLike(BaseModel):
    user_id: str
    before_count_text: str | None
    after_count_text: str
    submitted: bool


class ReverseWSStatus(BaseModel):
    enabled: bool = False
    url: str = ""
    role: Literal["Universal", "API", "Event"] = "Universal"
    state: Literal["disabled", "connecting", "connected", "reconnecting", "stopped"] = (
        "disabled"
    )
    last_error: str | None = None


class RuntimeStatus(BaseModel):
    account_id: str
    label: str
    connected: bool
    listening: bool
    mode: str
    targets: list[Target]
    last_error: str | None
    listener_state: str = "stopped"
    reverse_ws: ReverseWSStatus = Field(default_factory=ReverseWSStatus)


class CalendarDay(BaseModel):
    label: str
    enabled: bool


class HistoryCalendar(BaseModel):
    conversation_id: str
    month: str
    days: list[CalendarDay]


@dataclass(frozen=True)
class Action:
    name: str
    params: type[BaseModel]
    result: object
    mutating: bool
    summary: str
    added: bool = False
    dispatch: Literal["onebot", "native"] = "onebot"


ACTIONS: dict[str, Action] = {}


def add(
    name: str,
    result: object,
    fields: dict | None = None,
    *,
    write=False,
    summary="",
    added=False,
    dispatch="onebot",
):
    model = create_model("Params_" + name, __base__=Params, **(fields or {}))
    ACTIONS[name] = Action(name, model, result, write, summary or name, added, dispatch)


# Field definitions are shared, actions remain explicit and auditable.
target = {"target_id": (Identifier, ...)}
group = {"group_id": (Identifier, ...)}
private = {"user_id": (Identifier, ...)}
message = {"message_id": (Identifier, ...)}
enabled = {"enabled": (StrictBool, ...)}
expected_text = {"expected_text": (str | None, None)}
entry = {"expected": (GroupFileEntry | None, None)}
named = {**target, "name": (Text, ...)}
member = {**target, "member_name": (Text, ...), "expected_user_id": (Identifier, ...)}
notice = {**target, "text": (Text, ...), "expected": (GroupNotice | None, None)}

add("get_login_info", LoginInfo)
add("get_version_info", VersionInfo)
add("x_get_client_version", ClientVersion, summary="读取QQ客户端版本")
add("x_get_storage_paths", StoragePaths, summary="读取QQ存储路径")
for name in ("x_get_capabilities", "x_get_capability_matrix"):
    add(name, CapabilitySummary, summary="读取已实现接口能力")
for name in ("can_send_image", "can_send_record"):
    add(name, create_model("Result_" + name, yes=(bool, ...)))
add("x_get_targets", list[Target], summary="读取配置会话")
add(
    "x_discover_conversations",
    list[ConversationCandidate],
    summary="探查已加载会话",
    dispatch="native",
)
add(
    "x_resolve_conversation",
    Target,
    {"name": (Text, ...)},
    summary="读取所选会话号码与类型",
    dispatch="native",
)
add("x_get_uia_status", InstanceStatus, summary="读取原生窗口状态")
add("x_get_runtime_status", RuntimeStatus, summary="读取账号运行状态")
add("x_get_api_catalog", ApiCatalog, summary="读取接口与类型目录")
for name, summary in (
    ("x_connect", "绑定QQ账号"),
    ("x_disconnect", "解除QQ绑定"),
    ("x_start_listener", "开始监听"),
    ("x_stop_listener", "停止监听"),
):
    add(name, RuntimeStatus, write=True, summary=summary)

for name, scope in (
    ("send_group_msg", group),
    ("send_private_msg", private),
    (
        "send_msg",
        {
            "message_type": (Literal["group", "private"] | None, None),
            "group_id": (Identifier | None, None),
            "user_id": (Identifier | None, None),
        },
    ),
):
    add(
        name,
        MessageId,
        {
            **scope,
            "message": (str | list[JsonObject], ...),
            "auto_escape": (StrictBool, False),
        },
        write=True,
    )
add("get_msg", ProtocolMessage, message)
add("get_group_info", GroupInfo, {**group, "no_cache": (StrictBool, False)})
add("get_group_member_list", list[MemberInfo], group)
add(
    "get_group_member_info",
    MemberInfo,
    {**group, **private, "no_cache": (StrictBool, False)},
)
add("group_poke", type(None), {**group, **private}, write=True)
add("x_poke_member", MemberPokeReceipt, member, write=True)
add("x_poke_friend", Message, target, write=True)
add(
    "set_group_card",
    type(None),
    {**group, "user_id": (Identifier | None, None), "card": (str, "")},
    write=True,
)
add(
    "x_set_member_card", MemberCardReceipt, {**member, "value": (Text, ...)}, write=True
)
add(
    "set_group_whole_ban",
    type(None),
    {**group, "enable": (StrictBool, True)},
    write=True,
)
add(
    "set_group_ban",
    type(None),
    {**group, **private, "duration": (StrictInt, 1800)},
    write=True,
)
add(
    "set_group_admin",
    type(None),
    {**group, **private, "enable": (StrictBool, True)},
    write=True,
)
add(
    "set_group_kick",
    c.MemberRemovalReceipt,
    {**group, **private, "reject_add_request": (StrictBool, False)},
    write=True,
)
add(
    "set_group_leave",
    c.GroupLeaveReceipt,
    {**group, "is_dismiss": (StrictBool, False)},
    write=True,
)
add(
    "x_set_member_ban",
    c.MemberBanReceipt,
    {**target, "member_name": (Text, ...), "duration": (StrictInt, ...)},
    write=True,
)
add("x_set_member_admin", MemberAdminReceipt, {**member, **enabled}, write=True)
add("x_kick_member", c.MemberRemovalReceipt, member, write=True)
for name in ("delete_msg", "set_essence_msg", "delete_essence_msg"):
    add(name, type(None), {**message, **expected_text}, write=True)
add("x_get_message_essence", Enabled, message)
add("x_get_reactions", MessageReactions, message)
add(
    "x_add_reaction",
    ReactionReceipt,
    {**message, **expected_text, "label": (Text, ...)},
    write=True,
)
add("x_remove_own_reaction", ReactionReceipt, {**message, **expected_text}, write=True)
add("x_get_group_preferences", c.GroupPreferences, target)
for name in ("x_set_muted", "x_set_pinned", "x_set_blocked", "x_set_auto_download"):
    add(name, c.SettingReceipt, {**target, **enabled}, write=True)
add("x_get_auto_download", Enabled, target)
add(
    "x_set_group_speak_limit",
    c.SettingReceipt,
    {**target, "per_minute": (Literal[0, 5, 10], ...)},
    write=True,
)
add(
    "x_set_group_message_mode",
    c.SettingReceipt,
    {**target, "mode": (Literal["notify", "silent", "assistant", "blocked"], ...)},
    write=True,
)
for name, scope in (
    ("forward_group_single_msg", group),
    ("forward_friend_single_msg", private),
):
    add(name, MessageId, {**scope, **message}, write=True)
add(
    "x_get_menu",
    list[c.MenuAction],
    {"target_id": (Identifier | None, None), "area": (Text, ...)},
)
add(
    "x_get_notifications",
    list[SystemCard],
    {"kind": (Literal["friend", "group"], "group")},
)
for name in ("set_friend_add_request", "set_group_add_request"):
    add(
        name,
        RequestDecision,
        {
            "flag": (Text, ...),
            "approve": (StrictBool, True),
            "sub_type": (str, "add"),
            "remark": (str, ""),
            "reason": (str, ""),
        },
        write=True,
    )
add("x_mark_unread", c.SettingReceipt, target, write=True)
add(
    "x_send_face",
    MessageId,
    {**target, "label": (Text, ...), "text": (str, "")},
    write=True,
)
add("x_get_group_applications", list[c.MenuAction], target)
add(
    "x_get_member_submenu",
    c.SubmenuInfo,
    {**target, "member": (Text, ...), "entry": (Text, ...)},
)
add(
    "x_get_group_files",
    c.GroupFilesView,
    {
        **target,
        "query": (str | None, None),
        "order": (Literal["newest", "oldest", "size_desc", "size_asc"] | None, None),
    },
)
add("x_get_group_notices", GroupNotices, target)
add(
    "x_publish_group_notice",
    GroupNotice,
    {**target, "text": (Text, ...), "image": (str | None, None)},
    write=True,
)
add(
    "x_edit_group_notice",
    GroupNoticeMutationReceipt,
    {
        **notice,
        "new_text": (Text, ...),
        "image": (str | None, None),
        "remove_image": (StrictBool, False),
    },
    write=True,
)
add("x_delete_group_notice", GroupNoticeMutationReceipt, notice, write=True)
add("x_get_group_notice_pinned", Pinned, notice)
add(
    "x_set_group_notice_pinned",
    GroupNoticePinReceipt,
    {**notice, **enabled},
    write=True,
)
add("x_get_group_essences", GroupEssences, target)
add("x_get_group_join_policy", GroupJoinPolicy, target)
for field, value_type in {
    "join_mode": Literal["open", "verify", "closed"],
    "member_invites": StrictBool,
    "invite_approval": Literal["direct", "admin", "under100"],
    "bot_invites": StrictBool,
    "bot_invite_approval": Literal["direct", "admin"],
}.items():
    # The native policy parser remains the source of accepted client labels.
    add(
        "x_set_group_" + field,
        GroupJoinPolicyReceipt,
        {**target, "value": (value_type, ...)},
        write=True,
    )
add(
    "x_get_group_description",
    create_model("Description", description=(str, ...)),
    target,
)
add(
    "x_set_group_description",
    c.SettingReceipt,
    {**target, "value": (str, ...)},
    write=True,
)
add("x_get_group_tags", create_model("Tags", labels=(list[str], ...)), target)
add("x_get_group_share_link", create_model("ShareLink", url=(str, ...)), target)
add(
    "x_set_group_tag",
    GroupTagReceipt,
    {**target, "label": (Annotated[str, Field(min_length=1, max_length=8)], ...), **enabled},
    write=True,
)
add(
    "x_get_group_file_entries",
    GroupFileListing,
    {**target, "folder": (str | None, None)},
)
add("x_create_group_folder", GroupFileMutationReceipt, named, write=True)
add(
    "x_upload_group_file",
    GroupFileMutationReceipt,
    {**target, "path": (Text, ...)},
    write=True,
)
for name in ("x_rename_group_folder", "x_rename_group_file"):
    add(
        name,
        GroupFileMutationReceipt,
        {**named, **entry, "new_name": (Text, ...)},
        write=True,
    )
for name in ("x_delete_group_folder", "x_delete_group_file"):
    add(name, GroupFileMutationReceipt, {**named, **entry}, write=True)
add(
    "x_move_group_file",
    GroupFileMutationReceipt,
    {
        **named,
        **entry,
        "folder": (str | None, None),
        "source_folder": (str | None, None),
    },
    write=True,
)
add(
    "x_download_group_file",
    GroupFileDownloadReceipt,
    {**named, **entry, "destination": (Text, ...)},
    write=True,
)
add(
    "x_get_group_file_actions",
    list[c.MenuAction],
    {**target, "row_index": (StrictInt, ...), "query": (str | None, None)},
)
add("x_get_group_file_sort_options", list[c.FileSortChoice], target)
add("x_get_group_file_sort_state", c.FileSortState, target)
add("x_get_message_secondary", c.MessageMenuInfo, {**target, **message})
add("x_get_reaction_palette", c.ReactionPanel, {**target, **message})
add(
    "x_get_controls",
    list[c.ControlInfo],
    {**target, "area": (Literal["chat", "settings", "members"], "chat")},
)
add("x_get_chat_settings", c.ChatSettings, target)
add("x_get_draft", c.DraftState, target)
add("x_set_draft", c.DraftState, {**target, "text": (str, ...)}, write=True)
add("x_clear_draft", c.DraftState, {**target, "expected_text": (str, ...)}, write=True)
add("x_send_draft", MessageId, {**target, "expected_text": (Text, ...)}, write=True)
add("x_get_member_profile", c.MemberProfile, named)
add("x_get_member_actions", list[c.MenuAction], named)
add("x_get_message_actions", list[c.MenuAction], {**target, **message})
add("x_get_ui_message", Message, {**target, **message})
add(
    "x_get_history",
    list[HistoryRecord],
    {
        **target,
        "query": (str | None, None),
        "pages": (Annotated[int, Field(ge=1, le=50)], 1),
        "limit": (Annotated[int, Field(ge=1, le=1000)], 100),
        "category": (Literal["all", "files"], "all"),
        "sender": (str | None, None),
        "on_date": (str | None, None),
    },
)
add(
    "x_get_members",
    list[GroupMemberInfo],
    {**target, "query": (str | None, None), "resolve_ids": (StrictBool, False)},
)
add("x_send_file", MessageId, {**target, "file": (Text, ...)}, write=True)

# Previously GUI/Python-verified features, now available over WS and HTTP.
batch = {
    **target,
    "names": (Annotated[list[Text], Field(min_length=2, max_length=20)], ...),
    "expected": (dict[str, GroupFileEntry] | None, None),
}
add(
    "x_delete_group_files",
    list[GroupFileMutationReceipt],
    batch,
    write=True,
    added=True,
    dispatch="native",
    summary="批量删除群文件",
)
add(
    "x_download_group_files",
    list[GroupFileDownloadReceipt],
    {**batch, "destination": (Text, ...)},
    write=True,
    added=True,
    dispatch="native",
    summary="批量下载群文件",
)
add(
    "x_download_group_folder",
    list[GroupFileDownloadReceipt],
    {**named, **entry, "download_root": (Text, ...)},
    write=True,
    added=True,
    dispatch="native",
    summary="下载群文件夹",
)
add(
    "x_get_group_folder_properties",
    FolderProperties,
    {**named, **entry},
    added=True,
    dispatch="native",
    summary="群文件夹属性",
)
add(
    "x_save_group_qr",
    SavedQR,
    {**target, "destination": (Text, ...)},
    write=True,
    added=True,
    dispatch="native",
    summary="保存群二维码",
)
add(
    "x_share_group_qr",
    Message,
    {**target, "destination": (Identifier, ...)},
    write=True,
    added=True,
    dispatch="native",
    summary="分享群二维码",
)
add(
    "x_share_contact",
    Message,
    {**target, "destination": (Identifier, ...)},
    write=True,
    added=True,
    dispatch="native",
    summary="分享好友名片",
)
add(
    "x_get_friend_profile",
    ConversationInfo,
    {**target, "full": (StrictBool, False)},
    added=True,
    dispatch="native",
    summary="读取好友资料",
)
add(
    "x_like_friend",
    ProfileLike,
    target,
    write=True,
    added=True,
    dispatch="native",
    summary="好友资料单次点赞",
)
add(
    "x_get_contact_card_profile",
    ConversationInfo,
    {**target, "ui_message_id": (Text, ...), "expected_user_id": (Identifier, ...)},
    added=True,
    dispatch="native",
    summary="名片消息资料联查",
)
add(
    "x_get_favorite_stickers",
    list[FavoriteSticker],
    target,
    added=True,
    dispatch="native",
    summary="读取收藏表情",
)
add(
    "x_import_stickers",
    StickerImportReceipt,
    {
        **target,
        "paths": (list[Text], ...),
    },
    write=True,
    added=True,
    dispatch="native",
    summary="导入PNG或GIF表情",
)
selector = {**target, "selector": (str | FavoriteSticker, ...)}
add(
    "x_move_sticker_first",
    FavoriteSticker,
    selector,
    write=True,
    added=True,
    dispatch="native",
    summary="收藏表情置前",
)
add(
    "x_get_sticker_remark",
    str,
    selector,
    added=True,
    dispatch="native",
    summary="读取表情备注",
)
add(
    "x_set_sticker_remark",
    str,
    {**selector, "value": (Annotated[str, Field(min_length=1, max_length=15)], ...)},
    write=True,
    added=True,
    dispatch="native",
    summary="修改表情备注",
)
add(
    "x_delete_sticker",
    FavoriteSticker,
    selector,
    write=True,
    added=True,
    dispatch="native",
    summary="删除收藏表情",
)
add(
    "x_get_history_calendar",
    HistoryCalendar,
    {
        **target,
        "year": (Annotated[StrictInt, Field(ge=1900, le=2100)], ...),
        "month": (Annotated[StrictInt, Field(ge=1, le=12)], ...),
    },
    added=True,
    dispatch="native",
    summary="历史日期可选状态",
)
add(
    "x_locate_history_image",
    Message,
    {**target, "on_date": (Text, ...), "expected_message_id": (Text, ...)},
    added=True,
    dispatch="native",
    summary="定位私聊历史图片",
)
add(
    "x_get_messages",
    list[ObservedMessage],
    target,
    added=True,
    dispatch="native",
    summary="读取当前加载消息及本地协议ID",
)


def validate_result(action: Action, data: object) -> JsonValue:
    adapter = TypeAdapter(action.result)
    return adapter.dump_python(adapter.validate_python(data), mode="json")
