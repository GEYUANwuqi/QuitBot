# 已实现能力矩阵

Windows 与 Linux 共用下方业务动作目录。Windows 使用 UI Automation，Linux 使用 AT-SPI 与 X11；客户端是否显示入口、可用权限及已加载范围仍由实际 QQ 界面决定。Linux 无头启动与系统依赖见 [README](../README.md#快速开始)。

本页列出已有公开调用入口的能力。账号服务共提供 131 个动作，包含 QQ 业务、账号生命周期和目录查询；本机控制台服务另行提供登录、会话与配置接口。

## 业务能力与使用条件

| 领域 | 已实现 | 使用条件与结果范围 |
| --- | --- | --- |
| 消息发送 | 文字、单本地图片、文件、引用回复、单人 @、原生表情 | 配置内的群聊 / 私聊；已有草稿时拒绝覆盖，提交后读取原生回执 |
| 消息操作 | 单条转发、撤回、精华、贴表情读取 / 添加 / 移除本人回应 | 操作仍可定位的消息，受 QQ 权限和时间窗限制；贴表情添加要求当前账号尚未回应该消息 |
| 消息查询 | 当前加载列表、按本地协议 ID 查询、历史文本 / 文件筛选、日期和发送人过滤、历史日历 | 结果来自客户端已加载视图，历史筛选受页面加载范围限制 |
| 图片与文件资源 | 原始图片导出、OneBot 图片 / 普通表情消息、`get_image`、base64 读取、缓存图片再次发送；按消息下载文件 | 原图通过 QQ 复制操作提供，按账号缓存并保留 GIF；普通表情编号读取 QQ 富文本剪贴板；文件下载校验消息 ID 与文件名，目标须不存在 |
| QQ 网页登录 | 启动 QQ、保存账号选择、二维码读取 / 刷新、打开后台已登录账号、登录后核验与连接 | 完成控制台改密后使用，手机 QQ 负责扫码确认；Linux 已实测完整浏览器流程，Windows 登录页待交叉验证 |
| 历史图片 | 私聊图片按日期与预期 UI 消息键定位 | 日期内唯一已加载缩略图，定位后核验消息键和可见图片 |
| 会话状态 | 置顶、免打扰、标未读、草稿读写 / 清空 / 发送、私聊自动下载和屏蔽 | 状态由原生界面回读；草稿操作校验当前内容 |
| 会话探查 | 已加载最近会话与主窗口当前会话、名称筛选与勾选、所选会话号码及类型读取 | 可见同名会话拒绝自动选择；核验可滚动查找条目，使用 QQ 复制号码菜单并更新剪贴板；保存配置后才加入监听范围 |
| 群成员 | 已加载成员和资料、搜索、群名片、管理员设置、禁言 / 解除、踢人、戳一戳 | 成员列表范围以加载结果为准；涉及身份的操作核验唯一对象与数字 QQ 号，管理操作需要相应权限 |
| 群设置 | 全员禁言、发言限频、消息提醒模式、加群方式、成员与机器人邀请策略、群简介和标签 | 可选值以动作参数定义为准，修改后回读对应设置；群标签最多 8 个字符，最多保留 5 项 |
| 请求与退群 | 处理已观察到的好友 / 入群请求、提交退群 | 请求使用当前实例保存的 flag；退群回执的 `membership_verified=false` 表示成员关系尚未核验 |
| 群文件 | 列表、搜索 / 排序、菜单与排序状态、创建文件夹、重命名、移动、删除、上传和单文件下载 | 按名称与可见元信息定位；根目录提供完整操作，子目录支持读取与文件移动；Linux 排序支持最新 / 最旧时间，文件夹删除限已确认的空文件夹 |
| 批量文件 | 一次多选下载 / 删除 2–20 个根文件、文件夹属性、文件夹整体下载 | 名称须唯一，元信息须匹配；文件夹下载限完整加载的非空平面目录，输出路径须符合动作要求且避免覆盖已有文件 |
| 群公告 | 列表、文字 / 单 PNG 发布、编辑、删除、置顶读取 / 切换 | 使用唯一正文和可选 `expected` 定位；编辑可保留、替换或移除已有图片；保留当前列表中的最后一条公告 |
| 群分享 | 分享链接、二维码保存和原生图文转发 | 二维码保存为后端新路径下的 PNG，并返回大小与 SHA-256；二维码转发目标限配置内且能唯一核验的群 |
| 好友资料 | 资料卡 / 完整资料、单次点赞、名片分享、从名片消息核对 QQ 号 | 点赞返回界面计数文本；名片分享目标限配置内且能唯一核验的群，资料联查支持 QQ 好友名片消息 |
| 收藏表情 | 列表、PNG / GIF 导入、置前、备注读写和精确删除 | `selector` 使用唯一非默认备注或当前有效的完整表情对象；Windows / Linux 跨请求优先使用备注，导入返回数量、当前列表及可确认的新增对象，详见[接口字段](api.md#收藏表情与平台字段) |
| 通知与监听 | 消息轮询、请求卡片、部分系统通知、撤回 / 戳一戳 / 贴表情观察、可选成员列表差异 | 默认每个会话打开独立窗口，已核验窗口可复用；依赖 GUI 更新和加载；标准字段齐全时生成 OneBot 事件，其余通过 `quitbot` 扩展返回 |
| 控件观察 | 主界面 / 会话菜单、消息 / 成员二级菜单、群应用、反应面板和可访问控件属性 | 返回结构化观察结果，具体业务执行使用对应动作 |
| 账号服务 | 账号绑定 / 解除、状态、监听启停、目录查询、QQ 版本和存储路径读取 | 每个 QQ 号对应独立实例与连接配置；存储路径从设置界面读取，客户端未显示聊天数据目录时 `data_directory` 为 `null` |
| WebSocket 连接 | 正向服务、反向主动连接、Universal / API / Event 用途、握手令牌、断线重连与状态查询 | 按账号隔离并共用动作队列；反向配置支持热更新，断线后不重放动作或事件 |
| 登录与配置 | 密码登录、首次强制改密、会话查询及退出、配置读取与保存、热更新和应用状态查询 | 会话默认 4 小时、时长可配置，配置保存携带 `revision`，结果包含各账号应用状态 |
| 进程探查与账号连接 | 进入账号连接页枚举进程窗口、缓存读取、手动刷新；连接时读取所选窗口的 QQ 号 / 昵称 / 头像 | 探查仅返回进程名、PID 和 HWND；连接时读取资料并核验数字身份，成功后缓存资料供账号设置使用 |
| 控制台与连接设置 | 已激活账号列表、自定义 WebUI / HTTP / 正向 WS 监听地址与端口、反向 WS 接收端、登录有效期与保护 | 三种对外连接独立设置；WebUI 监听重启生效，其他设置热更新；控制台使用独立账号通道 |

Linux 的好友 / 入群请求处理、踢人与退群保留共用调用入口，实机验证待完成。

读取操作可能打开页面、切换会话或使用焦点。结果中的 `scope`、`complete`、`verified_in_ui`、`membership_verified` 等字段说明本次读取或执行的具体范围。

## 调用方式

账号动作可通过 WS 或 HTTP 调用，输入和返回类型共用同一套契约。登录、会话和配置动作见[接口说明](api.md#管理动作)，OneBot 参数与事件差异见[适配现状](onebot11.md)。

下表由账号契约生成。参数后的 `?` 表示可选，默认值、联合类型和字段结构以 OpenAPI 为准。“写”标记消息发送、状态修改及本地文件输出等操作。

## 完整动作目录

<!-- GENERATED_ACTIONS -->

| 动作 | 参数 | 返回 data | 写 |
| --- | --- | --- | --- |
| `get_login_info` | 无 | `LoginInfo` | 否 |
| `get_version_info` | 无 | `VersionInfo` | 否 |
| `x_get_client_version` | 无 | `ClientVersion` | 否 |
| `x_get_storage_paths` | 无 | `StoragePaths` | 否 |
| `x_get_capabilities` | 无 | `CapabilitySummary` | 否 |
| `x_get_capability_matrix` | 无 | `CapabilitySummary` | 否 |
| `can_send_image` | 无 | `Result_can_send_image` | 否 |
| `can_send_record` | 无 | `Result_can_send_record` | 否 |
| `x_get_targets` | 无 | `list[Target]` | 否 |
| `x_discover_conversations` | 无 | `list[ConversationCandidate]` | 否 |
| `x_resolve_conversation` | name | `Target` | 否 |
| `x_get_uia_status` | 无 | `InstanceStatus` | 否 |
| `x_get_runtime_status` | 无 | `RuntimeStatus` | 否 |
| `x_get_api_catalog` | 无 | `ApiCatalog` | 否 |
| `x_connect` | 无 | `RuntimeStatus` | 是 |
| `x_disconnect` | 无 | `RuntimeStatus` | 是 |
| `x_start_listener` | 无 | `RuntimeStatus` | 是 |
| `x_stop_listener` | 无 | `RuntimeStatus` | 是 |
| `send_group_msg` | group_id, message, auto_escape? | `MessageId` | 是 |
| `send_private_msg` | user_id, message, auto_escape? | `MessageId` | 是 |
| `send_msg` | message_type?, group_id?, user_id?, message, auto_escape? | `MessageId` | 是 |
| `get_msg` | message_id | `ProtocolMessage` | 否 |
| `x_download_message_file` | message_id, destination, expected_name | `MessageFileDownload` | 是 |
| `get_image` | file | `ImageFile` | 否 |
| `x_get_image` | file | `ImageContent` | 否 |
| `get_group_info` | group_id, no_cache? | `GroupInfo` | 否 |
| `get_group_member_list` | group_id | `list[MemberInfo]` | 否 |
| `get_group_member_info` | group_id, user_id, no_cache? | `MemberInfo` | 否 |
| `group_poke` | group_id, user_id | `null` | 是 |
| `x_poke_member` | target_id, member_name, expected_user_id | `MemberPokeReceipt` | 是 |
| `x_poke_friend` | target_id | `Message` | 是 |
| `set_group_card` | group_id, user_id?, card? | `null` | 是 |
| `x_set_member_card` | target_id, member_name, expected_user_id, value | `MemberCardReceipt` | 是 |
| `set_group_whole_ban` | group_id, enable? | `null` | 是 |
| `set_group_ban` | group_id, user_id, duration? | `null` | 是 |
| `set_group_admin` | group_id, user_id, enable? | `null` | 是 |
| `set_group_kick` | group_id, user_id, reject_add_request? | `MemberRemovalReceipt` | 是 |
| `set_group_leave` | group_id, is_dismiss? | `GroupLeaveReceipt` | 是 |
| `x_set_member_ban` | target_id, member_name, duration | `MemberBanReceipt` | 是 |
| `x_set_member_admin` | target_id, member_name, expected_user_id, enabled | `MemberAdminReceipt` | 是 |
| `x_kick_member` | target_id, member_name, expected_user_id | `MemberRemovalReceipt` | 是 |
| `delete_msg` | message_id, expected_text? | `null` | 是 |
| `set_essence_msg` | message_id, expected_text? | `null` | 是 |
| `delete_essence_msg` | message_id, expected_text? | `null` | 是 |
| `x_get_message_essence` | message_id | `Enabled` | 否 |
| `x_get_reactions` | message_id | `MessageReactions` | 否 |
| `x_add_reaction` | message_id, expected_text?, label | `ReactionReceipt` | 是 |
| `x_remove_own_reaction` | message_id, expected_text? | `ReactionReceipt` | 是 |
| `x_get_group_preferences` | target_id | `GroupPreferences` | 否 |
| `x_set_muted` | target_id, enabled | `SettingReceipt` | 是 |
| `x_set_pinned` | target_id, enabled | `SettingReceipt` | 是 |
| `x_set_blocked` | target_id, enabled | `SettingReceipt` | 是 |
| `x_set_auto_download` | target_id, enabled | `SettingReceipt` | 是 |
| `x_get_auto_download` | target_id | `Enabled` | 否 |
| `x_set_group_speak_limit` | target_id, per_minute | `SettingReceipt` | 是 |
| `x_set_group_message_mode` | target_id, mode | `SettingReceipt` | 是 |
| `forward_group_single_msg` | group_id, message_id | `MessageId` | 是 |
| `forward_friend_single_msg` | user_id, message_id | `MessageId` | 是 |
| `x_get_menu` | target_id?, area | `list[MenuAction]` | 否 |
| `x_get_notifications` | kind? | `list[SystemCard]` | 否 |
| `set_friend_add_request` | flag, approve?, sub_type?, remark?, reason? | `RequestDecision` | 是 |
| `set_group_add_request` | flag, approve?, sub_type?, remark?, reason? | `RequestDecision` | 是 |
| `x_mark_unread` | target_id | `SettingReceipt` | 是 |
| `x_send_face` | target_id, label, text? | `MessageId` | 是 |
| `x_get_group_applications` | target_id | `list[MenuAction]` | 否 |
| `x_get_member_submenu` | target_id, member, entry | `SubmenuInfo` | 否 |
| `x_get_group_files` | target_id, query?, order? | `GroupFilesView` | 否 |
| `x_get_group_notices` | target_id | `GroupNotices` | 否 |
| `x_publish_group_notice` | target_id, text, image? | `GroupNotice` | 是 |
| `x_edit_group_notice` | target_id, text, expected?, new_text, image?, remove_image? | `GroupNoticeMutationReceipt` | 是 |
| `x_delete_group_notice` | target_id, text, expected? | `GroupNoticeMutationReceipt` | 是 |
| `x_get_group_notice_pinned` | target_id, text, expected? | `Pinned` | 否 |
| `x_set_group_notice_pinned` | target_id, text, expected?, enabled | `GroupNoticePinReceipt` | 是 |
| `x_get_group_essences` | target_id | `GroupEssences` | 否 |
| `x_get_group_join_policy` | target_id | `GroupJoinPolicy` | 否 |
| `x_set_group_join_mode` | target_id, value | `GroupJoinPolicyReceipt` | 是 |
| `x_set_group_member_invites` | target_id, value | `GroupJoinPolicyReceipt` | 是 |
| `x_set_group_invite_approval` | target_id, value | `GroupJoinPolicyReceipt` | 是 |
| `x_set_group_bot_invites` | target_id, value | `GroupJoinPolicyReceipt` | 是 |
| `x_set_group_bot_invite_approval` | target_id, value | `GroupJoinPolicyReceipt` | 是 |
| `x_get_group_description` | target_id | `Description` | 否 |
| `x_set_group_description` | target_id, value | `SettingReceipt` | 是 |
| `x_get_group_tags` | target_id | `Tags` | 否 |
| `x_get_group_share_link` | target_id | `ShareLink` | 否 |
| `x_set_group_tag` | target_id, label, enabled | `GroupTagReceipt` | 是 |
| `x_get_group_file_entries` | target_id, folder? | `GroupFileListing` | 否 |
| `x_create_group_folder` | target_id, name | `GroupFileMutationReceipt` | 是 |
| `x_upload_group_file` | target_id, path | `GroupFileMutationReceipt` | 是 |
| `x_rename_group_folder` | target_id, name, expected?, new_name | `GroupFileMutationReceipt` | 是 |
| `x_rename_group_file` | target_id, name, expected?, new_name | `GroupFileMutationReceipt` | 是 |
| `x_delete_group_folder` | target_id, name, expected? | `GroupFileMutationReceipt` | 是 |
| `x_delete_group_file` | target_id, name, expected? | `GroupFileMutationReceipt` | 是 |
| `x_move_group_file` | target_id, name, expected?, folder?, source_folder? | `GroupFileMutationReceipt` | 是 |
| `x_download_group_file` | target_id, name, expected?, destination | `GroupFileDownloadReceipt` | 是 |
| `x_get_group_file_actions` | target_id, row_index, query? | `list[MenuAction]` | 否 |
| `x_get_group_file_sort_options` | target_id | `list[FileSortChoice]` | 否 |
| `x_get_group_file_sort_state` | target_id | `FileSortState` | 否 |
| `x_get_message_secondary` | target_id, message_id | `MessageMenuInfo` | 否 |
| `x_get_reaction_palette` | target_id, message_id | `ReactionPanel` | 否 |
| `x_get_controls` | target_id, area? | `list[ControlInfo]` | 否 |
| `x_get_chat_settings` | target_id | `ChatSettings` | 否 |
| `x_get_draft` | target_id | `DraftState` | 否 |
| `x_set_draft` | target_id, text | `DraftState` | 是 |
| `x_clear_draft` | target_id, expected_text | `DraftState` | 是 |
| `x_send_draft` | target_id, expected_text | `MessageId` | 是 |
| `x_get_member_profile` | target_id, name | `MemberProfile` | 否 |
| `x_get_member_actions` | target_id, name | `list[MenuAction]` | 否 |
| `x_get_message_actions` | target_id, message_id | `list[MenuAction]` | 否 |
| `x_get_ui_message` | target_id, message_id | `Message` | 否 |
| `x_get_history` | target_id, query?, pages?, limit?, category?, sender?, on_date? | `list[HistoryRecord]` | 否 |
| `x_get_members` | target_id, query?, resolve_ids? | `list[GroupMemberInfo]` | 否 |
| `x_send_file` | target_id, file | `MessageId` | 是 |
| `x_delete_group_files` | target_id, names, expected? | `list[GroupFileMutationReceipt]` | 是 |
| `x_download_group_files` | target_id, names, expected?, destination | `list[GroupFileDownloadReceipt]` | 是 |
| `x_download_group_folder` | target_id, name, expected?, download_root | `list[GroupFileDownloadReceipt]` | 是 |
| `x_get_group_folder_properties` | target_id, name, expected? | `FolderProperties` | 否 |
| `x_save_group_qr` | target_id, destination | `SavedQR` | 是 |
| `x_share_group_qr` | target_id, destination | `Message` | 是 |
| `x_share_contact` | target_id, destination | `Message` | 是 |
| `x_get_friend_profile` | target_id, full? | `ConversationInfo` | 否 |
| `x_like_friend` | target_id | `ProfileLike` | 是 |
| `x_get_contact_card_profile` | target_id, ui_message_id, expected_user_id | `ConversationInfo` | 否 |
| `x_get_favorite_stickers` | target_id | `list[FavoriteSticker]` | 否 |
| `x_import_stickers` | target_id, paths | `StickerImportReceipt` | 是 |
| `x_move_sticker_first` | target_id, selector | `FavoriteSticker` | 是 |
| `x_get_sticker_remark` | target_id, selector | `str` | 否 |
| `x_set_sticker_remark` | target_id, selector, value | `str` | 是 |
| `x_delete_sticker` | target_id, selector | `FavoriteSticker` | 是 |
| `x_get_history_calendar` | target_id, year, month | `HistoryCalendar` | 否 |
| `x_locate_history_image` | target_id, on_date, expected_message_id | `Message` | 否 |
| `x_get_messages` | target_id | `list[ObservedMessage]` | 否 |
