"""Native UIA capability registry."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    name: str
    available: bool
    note: str
    state: str = "partial"
    category: str = "chat"


CAPABILITIES = (
    Capability(
        "group_notice_pin", True,
        "需编辑权限，唯一正文及可选expected核验；文字/单PNG公告编辑器Toggle设置及重新读取；图片置顶一次回执未知但独立回读确认，取消WS成功；不自动重试",
        category="group",
    ),
    Capability(
        "group_notice_edit", True,
        "文字/已有单PNG公告，保图改文/本地PNG替换/移图，保持置顶；互斥参数和expected校验，一次发布后重开核验；纯文字追加图片尚未开放",
        category="group",
    ),
    Capability(
        "group_notice_delete", True,
        "唯一正文及可选expected元信息核验，单次删除后重开确认；拒绝仅剩一条的可见列表，SDK文字公告已实测，无协议公告ID",
        category="group",
    ),
    Capability(
        "group_files_batch_delete", True,
        "2..20个唯一根文件，原生多选集合及元信息二次核验、精确数量确认、单次提交和全体消失回读；普通成员自有双文件已实测",
        category="group",
    ),
    Capability(
        "storage_paths", True,
        "原生存储设置读取下载目录和聊天数据目录；只返回UI路径，不打开目录或读取数据库",
        category="runtime",
    ),
    Capability(
        "client_version", True,
        "原生设置→关于QQ读取版本原文；保留既有设置窗口、恢复原设置页，仅关闭自己新建的窗口",
        category="runtime",
    ),
    Capability(
        "group_folder_properties",
        True,
        "根目录唯一文件夹名称、数量及可见创建/更新字段；保留原生文本；SDK同步入口已实机复测",
        category="group",
    ),
    Capability(
        "group_files_batch_download",
        True,
        "2..20个唯一根文件，原生精确多选；新建目录、落盘大小与本地SHA256；实测样本为双文件；SDK同步入口已实机复测",
        category="group",
    ),
    Capability(
        "group_folder_download",
        True,
        "完整加载的非空平面文件夹；调用者提供QQ当前下载根目录，拒绝已有同名输出；实测单文件及双文件目录；SDK同步入口已实机复测",
        category="group",
    ),
    Capability(
        "group_qr_save",
        True,
        "核验群号并原生保存PNG至新路径，返回大小和本地SHA256；SDK同步入口已实机复测",
        category="group",
    ),
    Capability(
        "group_qr_share",
        True,
        "原生二维码转发到唯一已核验群；等待新mixed消息及群名链接正文；SDK同步入口已实机复测",
        category="group",
    ),
    Capability(
        "contact_share",
        True,
        "数字好友资料卡→唯一已核验群→新名片摘要回读，仅群接收场景；SDK同步入口已实机复测",
        category="friend",
    ),
    Capability(
        "contact_card_profile",
        True,
        "仅com_tencent_contact_lua名片；内部Invoke打开资料，核验expected_user_id，不从摘要猜QQ号；SDK同步入口已实机复测",
        category="chat",
    ),
    Capability(
        "friend_profile",
        True,
        "紧凑资料卡或完整个人主页可见字段，核验数字QQ号并回到私聊；SDK同步入口已实机复测",
        category="friend",
    ),
    Capability(
        "friend_like",
        True,
        "单次提交、计数变化及重开回读；缺少旧计数保留None，不承诺可取消；SDK同步入口已实机复测",
        category="friend",
    ),
    Capability(
        "favorite_stickers",
        True,
        "当前加载集合，返回账号/HWND/RuntimeId/名称/顺序；非协议ID，失效引用拒绝使用；SDK同步入口已实机复测",
        category="chat",
    ),
    Capability(
        "sticker_import",
        True,
        "本地PNG/GIF原生文件导入，数量及已有UI身份校验；返回顺序不映射输入路径；双PNG及单GIF同步SDK已实测，GIF保真验证后恢复收藏",
        category="chat",
    ),
    Capability(
        "sticker_reorder",
        True,
        "唯一非默认备注或当前UI引用定位，原生菜单提交并核验首项身份；SDK同步入口已实机复测",
        category="chat",
    ),
    Capability(
        "sticker_remark",
        True,
        "1..15字唯一备注，检查失焦规范化，保存后重开读取；输入被QQ改变时取消；SDK同步入口已实机复测",
        category="chat",
    ),
    Capability(
        "sticker_delete",
        True,
        "唯一名称或有效UI引用；删除后检查数量及其余UI身份集合；SDK同步入口已实机复测",
        category="chat",
    ),
    Capability(
        "history_calendar",
        True,
        "原生月份/年份网格及年份分页，读取实际年月和日期启用状态；不可访问日期不等同历史为空；SDK同步入口已实机复测",
        category="chat",
    ),
    Capability(
        "history_image_locate",
        True,
        "日期内唯一已加载缩略图，定位回聊天后核验expected_message_id及可见图片；不适配多图消歧；SDK同步入口已实机复测",
        category="chat",
    ),
    Capability(
        "friend_poke",
        True,
        "已核验私聊的对方消息头像UIA3菜单；单次提交及新系统行回读；需已加载对方消息",
        category="friend",
    ),
    Capability(
        "group_share_link",
        True,
        "原生二维码面板复制、群号及新剪贴板验证；链接可能变化，会覆盖自动化会话剪贴板",
        category="group",
    ),
    Capability(
        "group_tags",
        True,
        "原生群标签列表、单项增删、幂等与保存后重开回读",
        category="group",
    ),
    Capability(
        "group_join_mode",
        True,
        "允许加入/身份验证/禁止加入；保留现有验证方式；完整策略前后快照与重新打开读回",
        category="group",
    ),
    Capability(
        "group_member_invites",
        True,
        "原生权限开关；重新开启可能重置审批规则，回执包含连带变化",
        category="group",
    ),
    Capability(
        "group_invite_approval",
        True,
        "直接进群/管理员审核/100人内免审核；按邀请权限分区定位下拉框",
        category="group",
    ),
    Capability(
        "group_bot_invites",
        True,
        "原生机器人邀请开关，修改后重新打开验证；不执行添加机器人",
        category="group",
    ),
    Capability(
        "group_bot_invite_approval",
        True,
        "直接进群/管理员审核；独立于好友邀请审批，返回完整策略变化",
        category="group",
    ),
    Capability(
        "group_description",
        True,
        "群资料设置中的原生编辑器；保存后重新打开验证",
        category="group",
    ),
    Capability(
        "group_notice_publish",
        True,
        "1–600字、非置顶；Python/async及WS可附一张本地PNG，发布后重开确认正文与媒体，保留既有编辑窗口；CLI仅文字",
        category="group",
    ),
    Capability(
        "group_essences",
        True,
        "独立窗口当前加载的正文摘要、发送者及设置者时间原文；支持已核验空态，不生成协议ID",
        category="group",
    ),
    Capability(
        "send_face",
        True,
        "按默认表情控件名称选择，支持单表情及附带文字；接收记录face_count，表情协议ID未知时不伪造",
        category="chat",
    ),
    Capability(
        "mark_unread",
        True,
        "群聊及私聊的会话右键菜单；提交后读回原生未读标记",
        category="chat",
    ),
    Capability(
        "group_notices",
        True,
        "Loaded announcement text, visible author/time and media presence; no server IDs or image URLs",
        category="group",
    ),
    Capability("send_text", True, "Unicode text; local UI receipt"),
    Capability("send_image", True, "One local image through QQ's picker"),
    Capability("send_file", True, "One local file with filename/status checks"),
    Capability(
        "quote_reply",
        True,
        "Loaded text/mixed messages and observed QQ contact-card quotes; card SDK replay pending",
    ),
    Capability("get_message", True, "Only messages still loaded in UI"),
    Capability("history", True, "Bounded UI pages, no protocol message IDs"),
    Capability(
        "members",
        True,
        "Loaded roster, optional real QQ profile resolution",
        category="group",
    ),
    Capability(
        "member_profiles",
        True,
        "Actual labelled QQ number and visible profile",
        category="group",
    ),
    Capability(
        "member_search",
        True,
        "Native roster search, restores previous query",
        category="group",
    ),
    Capability(
        "mention", True, "One member via native TA menu, then text", category="group"
    ),
    Capability(
        "forward",
        True,
        "Native plain-text forwarding; QQ contact-card multiselect forwarding to a verified group (SDK replay pending)",
    ),
    Capability("draft_read", True, "Text, quote, media and native mention indicators"),
    Capability("draft_write", True, "Write only into empty draft; expected-text clear"),
    Capability("draft_send", True, "Commit only exactly matched plain text"),
    Capability("chat_settings", True, "Read pin/mute/block/card/remark where exposed"),
    Capability("pin", True, "Idempotent TogglePattern with UI readback"),
    Capability(
        "own_group_card",
        True,
        "Only this account's group card, reopen verification",
        category="group",
    ),
    Capability(
        "control_catalog", True, "Read-only control metadata for chat/settings/members"
    ),
    Capability(
        "menu_inventory", True, "Message/member menu discovery and inspection plans"
    ),
    Capability(
        "navigation_menus",
        True,
        "Global add/more and conversation recent/files/capture/applications; read only",
    ),
    Capability(
        "group_applications",
        True,
        "Six-entry menu; focused Enter preferred, guarded hover fallback",
        category="group",
    ),
    Capability(
        "group_admin",
        True,
        "Fresh numeric member identity; native grant/revoke and roster/menu readback",
        category="group",
    ),
    Capability(
        "member_card",
        True,
        "Other-member nonempty card; fresh QQ identity and persisted editor readback",
        category="group",
    ),
    Capability(
        "group_poke",
        True,
        "Single native member poke; require new matching system row",
        category="group",
    ),
    Capability(
        "poke_notices",
        True,
        "Observed group/private poke system rows with verified actors; private peer and sender remain distinct",
        category="runtime",
    ),
    Capability(
        "reaction_notices",
        True,
        "Observed own-message reaction notifications with bound actor and optional emoji text; source/protocol IDs unknown",
        category="runtime",
    ),
    Capability(
        "group_file_entries",
        True,
        "Typed loaded root or one-level folder entries; no server IDs",
        category="group",
    ),
    Capability(
        "group_folder_create",
        True,
        "Create unique root folder through exact native dialog",
        category="group",
    ),
    Capability(
        "group_folder_rename",
        True,
        "Unique loaded root folder; native UIA3 context menu and full-name readback",
        category="group",
    ),
    Capability(
        "group_folder_delete",
        True,
        "Empty root folders only; exact native confirmation and loaded-row absence",
        category="group",
    ),
    Capability(
        "group_file_upload",
        True,
        "One local file through the group-file native picker",
        category="group",
    ),
    Capability(
        "group_file_rename",
        True,
        "Exact filename and metadata; preserve extension",
        category="group",
    ),
    Capability(
        "group_file_move",
        True,
        "Move between root and observed one-level folders with source/destination readback",
        category="group",
    ),
    Capability(
        "group_file_delete",
        True,
        "Exact unique filename and native confirmation; loaded-source absence",
        category="group",
    ),
    Capability(
        "group_file_download",
        True,
        "SaveAs to a new local destination; size and local SHA256",
        category="group",
    ),
    Capability(
        "group_files_read",
        True,
        "Loaded root rows or search suggestions; no server IDs/full traversal",
        category="group",
    ),
    Capability(
        "group_file_menus",
        True,
        "Read row actions and ExpandCollapse sort options; no file submission",
        category="group",
    ),
    Capability(
        "group_file_sort_state",
        True,
        "Read native checked sort choice; temporarily order loaded rows and restore",
        category="group",
    ),
    Capability(
        "member_submenus",
        True,
        "Read ban duration cascade; never invoke a business leaf",
        category="group",
    ),
    Capability(
        "reaction_palette",
        True,
        "Read message menu controls and loaded palette; no protocol emoji IDs",
    ),
    Capability(
        "reactions",
        True,
        "Live tooltip quick reactions and exact Unicode palette text; remove unique own chip; unknown chip IDs remain None",
        "partial",
    ),
    Capability(
        "events",
        True,
        "UI polling with observable source lifecycle",
        category="runtime",
    ),
    Capability(
        "detached_windows",
        True,
        "Verified per-conversation windows",
        category="runtime",
    ),
    Capability(
        "async",
        True,
        "One COM worker per instance; serialized UI transactions",
        category="runtime",
    ),
    Capability(
        "account_discovery",
        True,
        "Nickname/HWND and own-profile QQ identity",
        category="runtime",
    ),
    Capability(
        "onebot_ws", True, "Partial OneBot 11 forward/reverse WS", category="protocol"
    ),
    Capability(
        "recall",
        True,
        "Own loaded messages; expected-text guard and native recall system-row proof",
    ),
    Capability("delete_message", False, "Menu observed; no generic commit", "exposed"),
    Capability(
        "leave_group",
        True,
        "Native leave confirmation; membership requires independent readback",
        "partial",
        "group",
    ),
    Capability(
        "delete_friend",
        False,
        "Settings button observed; no commit",
        "exposed",
        "private",
    ),
    Capability("mute", True, "Group/private native mute switch with readback"),
    Capability(
        "group_preferences",
        True,
        "Visible whole-ban, speak limit and group notification mode",
        category="group",
    ),
    Capability(
        "whole_group_ban",
        True,
        "Native whole-group mute toggle and fresh readback",
        category="group",
    ),
    Capability(
        "group_speak_limit",
        True,
        "Native 0/5/10 messages-per-minute setting with readback",
        category="group",
    ),
    Capability(
        "group_message_mode",
        True,
        "notify/silent/assistant/blocked; fresh verified-window readback, contact rebind if invalidated",
        category="group",
    ),
    Capability(
        "auto_download",
        True,
        "Private receive-file auto-download switch with readback",
        category="private",
    ),
    Capability(
        "essence",
        True,
        "Native set/remove essence; actual context-menu state and cross-account view validated",
        category="group",
    ),
    Capability(
        "block",
        True,
        "Private 屏蔽此人 toggle with readback; not a protocol blacklist",
        "partial",
        "private",
    ),
    Capability("calls", False, "Toolbar observed; no call/capture endpoint", "exposed"),
    Capability(
        "group_moderation",
        True,
        "Native presets/custom whole minutes below 30 days; standard set_group_ban also verifies a fresh numeric member profile",
        "partial",
        "group",
    ),
    Capability(
        "kick_member",
        True,
        "Expected QQ number verified in profile, native confirmation, loaded-roster absence",
        category="group",
    ),
    Capability(
        "notifications",
        True,
        "Loaded friend/group notification cards with local opaque keys",
        category="runtime",
    ),
    Capability(
        "request_decision",
        True,
        "Approve/reject observed cards; friend dropdown validated, group applications not live-tested",
        category="runtime",
    ),
    Capability(
        "activity_events",
        True,
        "New UI system rows/cards over async/WS; initial history suppressed",
        category="runtime",
    ),
    Capability(
        "recall_notices",
        True,
        "Same-row group/friend recall correlated with an observed resolved sender; optional detached refresh",
        category="protocol",
    ),
    Capability(
        "server_online_status",
        False,
        "UI presence does not establish connectivity",
        "unsupported",
        "runtime",
    ),
)
