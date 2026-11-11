# OneBot v11 适配现状

QuitBot 使用 OneBot v11 的动作请求与回执格式，适配部分标准动作和事件。数据来自 QQ 已加载的原生界面，字段范围由可读取内容决定。

对照 [OneBot v11 公开 API](https://github.com/botuniverse/onebot-11/blob/master/api/public.md)，38 个标准动作名中已适配 20 个，另有 5 个社区扩展动作和 103 个 `x_` 动作，共 128 个账号动作。各动作支持的参数、返回值和条件见下表。

## 已适配的标准动作

| 动作 | 范围与差异 |
| --- | --- |
| `send_private_msg`、`send_group_msg`、`send_msg` | 配置内会话；纯文字、单本地图片、reply + 文字、群内单人 at + 文字。发送前校验完整消息形态；远程 URL、base64 媒体及多媒体混发尚未适配 |
| `delete_msg` | 撤回已映射且仍可定位的消息，受 QQ 权限和时间窗限制；可传 `expected_text` 核验内容 |
| `get_msg` | 查询原生已加载消息；发送人或媒体字段不足时可改用 `x_get_ui_message`。返回值使用本地消息 ID 和观察时间，缺少 QQ 协议 `real_id` |
| `get_login_info` | 返回绑定后已核验的 QQ 号与窗口昵称 |
| `get_group_info` | 返回群号、名称和可见资料 `fields`；完整人数和群容量字段尚未适配 |
| `get_group_member_info`、`get_group_member_list` | 已加载且解析到数字身份的成员，包含昵称、名片和角色；缺少加入时间、最后发言时间等字段 |
| `set_group_card` | 本人名片及有权限时的其他成员名片；修改其他成员前核验数字身份，其他成员名片须为非空值 |
| `set_group_whole_ban` | 操作全员禁言开关并回读 |
| `set_group_ban` | `duration` 单位为秒，0 表示解除，否则须为整分钟且少于 30 天；通过 UI 设置并回读 |
| `set_group_admin` | 设置 / 取消管理员，需要账号权限和唯一数字身份匹配 |
| `set_group_kick` | 踢人后回读已加载成员列表；`reject_add_request` 只接受 `false`，返回操作回执 |
| `set_group_leave` | 提交退群，`is_dismiss` 只接受 `false`；返回操作回执，`membership_verified=false` 表示成员关系尚未核验 |
| `set_friend_add_request`、`set_group_add_request` | 处理当前实例已观察并保存 flag 的请求；群请求支持 `sub_type=add`；附加备注、拒绝理由和邀请处理尚未适配；返回操作回执 |
| `can_send_image` | 返回 `yes=true`，对应单本地图片发送能力 |
| `can_send_record` | 返回 `yes=false`，语音发送尚未适配 |
| `get_version_info` | 返回 QuitBot 版本、v11 标识和 `partial/UIA` 元信息 |

`no_cache` 参数保留调用形式，数据仍从原生界面或已核验的短期身份映射读取。踢人、退群和请求处理的返回值使用操作回执，区别于标准定义中的 `null`。

## 扩展动作与连接

`group_poke`、`set_essence_msg`、`delete_essence_msg`、`forward_group_single_msg`、`forward_friend_single_msg` 是 5 个社区扩展动作。

群文件、公告、贴表情、资料、收藏表情、历史筛选、菜单观察及账号生命周期使用 `x_` 前缀，采用 QuitBot 的参数与返回类型。完整列表见[能力矩阵](capabilities.md)，精确类型见账号服务 OpenAPI。

`x_like_friend` 执行单次原生点赞并读取界面计数文本。`x_get_targets` 返回配置内的会话列表。这两个动作分别独立于标准 `send_like` 和好友 / 群列表查询。

正向 WS 使用 Bearer 请求头，或在首帧发送 `x_authenticate`。反向 WS 由 QuitBot 主动连接应用，握手携带 `X-Self-ID`、`X-Client-Role` 和可选接收端 Bearer 令牌，支持 Universal / API / Event 三种用途及断线重连。登录、会话、`x_get_config` 和 `x_set_config` 位于管理服务，独立于上述 128 个账号动作。连接格式见[接口调用](api.md)。

## 消息、通知与请求事件

| 事件 | 已实现范围 | 转换条件 |
| --- | --- | --- |
| `message.group / private` | 新出现的已加载文字消息、可解析的 at 文本 | 群发送人具有数字身份映射 |
| `quitbot.unresolved_message` | 媒体、卡片、随机表情，或身份、mention、face 字段不足的消息观察 | 使用 `reason` 和 `ui_message` 保留原因与原文 |
| `request.friend / group` | 新出现的 pending 请求卡片及当前实例 flag | 申请者与目标身份可确定；启动时已存在的卡片作为基线 |
| `notice.group_ban` | 禁言 / 解除禁言系统行 | 操作者和成员身份完整且唯一 |
| `notice.group_increase` | 可识别的邀请入群系统行 | 可见文本和数字身份足以关联入群对象 |
| `notice.group_recall / friend_recall` | 已见消息变为撤回行 | 能关联原消息及已确定的发送者 |
| `notice.notify / poke` | 群聊 / 私聊戳一戳系统行 | 能识别对应身份组合 |
| `quitbot.group_reaction_observation` | 系统行中的贴表情观察 | 源消息或表情协议 ID 不足时保留扩展结果 |
| `quitbot.system_observation` | 其余系统行 | 保留原文和会话信息 |
| `quitbot.loaded_roster_change` | 可选的已加载成员列表差异 | 仅描述已加载名单的变化 |
| `quitbot.listener_error` | 监听源停止或失败 | 修复运行环境后显式重启监听 |
| `meta_event.lifecycle.connect` | 认证后建立事件订阅 | 描述服务连接；QQ 绑定状态另行查询 |

默认消息源发送启动基线之后的新入站消息。开启 `activities` 才增加请求卡片和系统通知观察。`track_roster` 控制成员列表差异观察。

群消息生成标准事件需要发送人的数字 QQ 号。在「其他设置 → 高级配置」中，将对应账号的 `resolve_members` 设为 `true`，可通过群成员资料读取身份；也可配置 `member_bindings`。默认关闭自动解析时，未知发送人的消息通过 `quitbot.unresolved_message` 推送，`reason` 为 `sender_id_unavailable`，正文保存在 `ui_message.text`，前端归入「扩展事件」。

监听依赖 GUI 轮询和后台界面刷新，事件范围限本次可读取的内容。当前提供实时推送，标准心跳和断线补发尚未适配。

## 消息标识与时间

`message_id` 是按账号、会话和 UI 键持久化的本地正 int32。不同账号看到同一条消息时，分别使用各自索引中的 ID。引用、撤回和单条转发会检查消息所属会话。

界面时间文本保存在 `_quitbot.timestamp_text`，事件 `time` 使用本机观察时间。引用原文可保留在扩展数据中，协议级 quote ID 尚未适配。

原始 UI 消息键与协议 ID 的使用方式见[接口说明](api.md#消息-id-与文件路径)。

## 尚未适配的标准动作

以下 18 个标准动作尚无对应公开入口：

`get_forward_msg`、`send_like`、`set_group_anonymous_ban`、`set_group_anonymous`、`set_group_name`、`set_group_special_title`、`get_stranger_info`、`get_friend_list`、`get_group_list`、`get_group_honor_info`、`get_cookies`、`get_csrf_token`、`get_credentials`、`get_record`、`get_image`、`get_status`、`set_restart`、`clean_cache`。

服务与 UIA 状态可通过 `x_get_runtime_status`、`x_get_uia_status` 查询，反向连接状态位于运行状态的 `reverse_ws` 字段。
