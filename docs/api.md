# 接口调用与类型

QuitBot 提供管理服务和账号服务。控制台服务负责登录与配置，每个账号独立配置 HTTP 与正向 WS 监听端点。账号支持正向 WS（应用连接 QuitBot）和反向 WS（QuitBot 连接应用），两者共用动作契约、执行队列与事件源。

## 地址与路由

管理服务默认使用 `127.0.0.1:6699`：

| 入口 | 用途 | 认证 |
| --- | --- | --- |
| WS `/ws` | 登录、会话认证、改密、退出与配置 | 首帧登录 / 认证，或 Bearer 请求头 |
| HTTP `POST /auth/login` | 密码登录，返回 `SessionCredential` | 请求体中的 `password` |
| HTTP `GET /auth/session` | 会话到期时间与改密要求 | Bearer |
| HTTP `POST /auth/password` | 修改密码并换发会话 | Bearer |
| HTTP `POST /auth/logout` | 撤销当前会话 | Bearer |
| HTTP `GET /api/config` | 读取配置和应用状态 | Bearer |
| HTTP `PUT /api/config` | 保存并应用配置 | Bearer |
| HTTP `GET /api/discovery` | 读取最近一次探查缓存 | Bearer |
| HTTP `POST /api/discovery/refresh` | 枚举本图形会话的 QQ 进程窗口，仅返回进程元数据 | Bearer |
| HTTP `POST /api/accounts/activate` | 读取所选窗口的账号资料、保存配置并连接；参数为 `hwnd`、`revision` | Bearer |
| WS `/accounts/{account_id}/ws` | 控制台专用账号动作与事件通道 | 首帧认证或 Bearer |
| HTTP `GET /health` | 服务标识 | 无 |
| HTTP `/docs`、`/openapi.json` | 管理服务的 Swagger UI / OpenAPI | 无 |

账号 HTTP 与正向 WS 分别由 `http`、`forward_ws` 配置启用，默认端口值分别为 6700、6701。新账号默认关闭对外连接：

| 入口 | 用途 | 认证 |
| --- | --- | --- |
| WS `/ws`、`/` | 动作请求、回执与事件 | 首帧认证，或 Bearer 请求头 |
| WS `/api` | 动作请求与回执 | 同上 |
| WS `/event` | 事件接收 | 同上 |
| HTTP `POST /api/{action}` | 类型化动作调用 | Bearer |
| HTTP `GET /health` | 服务版本与固定账号 ID | Bearer |
| HTTP `/docs`、`/openapi.json` | 账号服务的 Swagger UI / OpenAPI | 无 |

账号 `/health` 用于确认服务身份，QQ 绑定和监听状态由 `x_get_runtime_status` 查询。

## 正向连接的登录与认证

在本机控制台服务的 WS 首帧发送：

```json
{"action":"x_login","params":{"password":"<登录密码>"},"echo":"login-1"}
```

成功回执的 `data` 包含 `access_token`、Unix 秒格式的 `expires_at` 和 `must_change_password`。HTTP `POST /auth/login` 使用相同参数，直接返回上述字段。

首次使用临时密码登录时，`must_change_password` 为 `true`，该会话只能查询会话、修改密码和退出。继续在同一 WS 发送：

```json
{"action":"x_change_password","params":{"new_password":"<新密码>"},"echo":"password-1"}
```

成功后使用回执中的新会话凭证，旧会话全部作废。后续修改密码还需传入 `current_password`。HTTP `POST /auth/password` 使用同一参数模型及 Bearer 会话认证。

HTTP 和可设置请求头的正向 WS 使用：

```http
Authorization: Bearer <access_token>
```

浏览器连接账号 WS 后，在 10 秒内发送认证首帧：

```json
{"action":"x_authenticate","params":{"access_token":"<会话凭证>"},"echo":"auth-1"}
```

认证成功回执返回会话到期时间与改密状态，等待 `retcode: 0` 再发送业务请求。请求头认证通过后可直接调用，事件订阅在认证成功后开始。

会话默认有效 4 小时，时长由 `webui.session_hours` 配置，对新登录生效；既有会话保持原到期时间，不随请求续期。后端重启、退出登录或改密也会使相关会话失效；空闲正向 WS 同样会在失效后关闭，关闭码为 `4401`。初始密码未修改时，配置及账号业务访问返回 `403` / `retcode: 1403`。登录失败返回 `401` / `1401`，连续失败达到限制时返回 `429` / `1429`。

凭证通过请求头或认证首帧传递，不写入 URL。密码及会话保存方式见[登录说明](configuration.md#登录密码与会话)。

## 会话探查与监听范围

绑定账号后，调用 `x_discover_conversations {}` 读取已加载的最近会话及主窗口当前会话。每项返回 `name`、`selectable` 和 `reason`；可见列表内的同名会话会标为不可选择。此步骤只枚举名称。

对选中的名称调用 `x_resolve_conversation {"name":"会话名称"}`，返回 `{id, name, kind}`。列表未加载对应条目时，通过原生控件滚动查找，再使用 QQ 的复制号码菜单读取数字身份，会更新当前图形会话的剪贴板；菜单缺失、号码无效或剪贴板变化时返回错误。两个接口均不修改 `targets`。将已核验结果写入账号配置并保存后，该会话才进入操作和监听范围。

监听默认使用独立聊天窗口，每个配置内会话打开一个窗口，已有窗口经过身份核验后复用。`x_start_listener` 返回 `starting` 时，窗口仍在初始化。通过 `x_get_runtime_status` 查询最终状态；失败时会发布 `quitbot.listener_error` 扩展事件，并在 `last_error` 中保留原因。

## 反向 WebSocket

在账号的 `reverse_ws` 中启用连接并填写应用提供的接收地址。每个账号独立连接一个接收端；多个账号连接同一接收地址时，通过握手中的 `X-Self-ID` 区分。HTTP 与正向 WS 可分别启用或关闭，控制台账号通道保持独立。

握手采用 [OneBot v11 反向 WS](https://github.com/botuniverse/onebot-11/blob/master/communication/ws-reverse.md) 的请求头：

| 请求头 | 值 |
| --- | --- |
| `X-Self-ID` | 当前配置的数字 QQ 号 |
| `X-Client-Role` | `Universal`、`API` 或 `Event` |
| `Authorization` | 设置接收端令牌时发送 `Bearer <reverse_ws.access_token>` |

接收端负责校验握手令牌，连接建立后直接发送动作请求和接收事件，无需 `x_authenticate` 首帧。该令牌由接收端提供，与 QuitBot 控制台的登录会话独立，不受控制台会话到期影响。

`Universal` 在同一连接上处理动作与事件；`API` 只处理动作；`Event` 只推送事件，忽略收到的动作。包含事件的连接会先收到 `meta_event.lifecycle.connect`。绑定与监听仍通过 `x_connect`、`x_start_listener` 控制。

断线后按 `reconnect_interval` 重连，默认 3 秒。重连建立新的订阅，已提交动作、丢失的回执和断线期间的事件均不重放。`x_get_runtime_status` 的 `reverse_ws` 字段返回地址、用途、状态和最近连接错误；状态为 `disabled`、`connecting`、`connected`、`reconnecting` 或 `stopped`。

配置字段见[反向 WS 配置](configuration.md#反向-ws-配置)。管理服务仅提供正向连接，反向连接只处理所属账号的业务。

## 账号动作

WS 请求使用 `action`、`params` 和可选的 `echo`：

```json
{"action":"x_get_runtime_status","params":{},"echo":"check-1"}
```

连接后先检查状态，调用 `x_connect` 绑定已登录的 QQ 窗口，按需调用 `x_start_listener` 启动监听。`x_get_api_catalog` 返回动作清单及参数、结果的 JSON Schema。

配置内的群可使用以下发送格式：

```json
{"action":"send_group_msg","params":{"group_id":"20001","message":"测试消息","auto_escape":true},"echo":"send-1"}
```

成功回执：

```json
{"status":"ok","retcode":0,"data":{"message_id":1},"echo":"send-1"}
```

`echo` 可使用 JSON 值，传入后会原样返回；省略时回执也省略该字段。事件使用 `post_type` 标识，字段范围见 [OneBot 适配](onebot11.md)。

HTTP 请求体直接填写动作参数：

```http
POST /api/x_get_group_file_entries
Content-Type: application/json
Authorization: Bearer <access_token>

{"target_id":"20001"}
```

账号动作的 HTTP 返回值同样使用 `status / retcode / data`。业务失败通常返回 HTTP 200，调用方需检查 `status` 和 `retcode`。

### 消息 ID 与文件路径

`x_get_messages` 返回 `[{message_id, message}]`。外层 `message_id` 是本账号的本地协议 ID，内层 `message.id` 是 UI 消息键。常规查询、撤回、回复、精华和贴表情动作使用协议 ID；`x_get_contact_card_profile.ui_message_id` 与 `x_locate_history_image.expected_message_id` 使用 UI 消息键。

文件路径均指**后端所在电脑**的路径，包含发送、上传、下载和图片保存。媒体发送范围为本地文件；远程 URL、base64 媒体和服务端文件 ID 尚未适配。

## 收藏表情与平台字段

`x_import_stickers` 返回 `{imported_count, collection, added, verified_in_ui}`：`collection` 是导入后的当前列表；仅在旧条目的标识保持有效、能够确定新增条目时，`added` 返回新增对象，否则为 `null`。调用方应按 `imported_count` 和回执判断结果。

表情操作的 `selector` 支持唯一的非默认备注，或完整 `FavoriteSticker` 对象。对象的 `account`、`hwnd`、`runtime_id` 和名称都参与核验；`index` 仅供展示。Windows 与 Linux QQ 都可能在关闭收藏面板、导入或删除后重建对象标识，跨请求操作请先在 QQ 中为目标设置唯一备注。`x_import_stickers` 的 `added` 是导入时的观察结果，后续请求仍会重新核验其有效性。失效引用会返回错误，写操作不会按序号重新选择。

修改备注时会等待 QQ 完成输入处理，再核对待提交文本。QQ 若去掉连字符等字符，接口会取消修改并返回错误，保留原备注；建议使用汉字、字母或数字组成的唯一备注。

`x_get_storage_paths.data_directory` 在客户端未显示数据目录时为 `null`。禁止加群时，QQ 隐藏的 `member_invites`、`bot_invites` 字段也返回 `null`。群文件排序读取当前平台实际提供的选项；Linux QQ 提供按最新、最旧时间排序。

## 管理动作

管理 WS 在登录或认证通过后提供以下动作：

| 动作 | 参数 | 返回 data |
| --- | --- | --- |
| `x_get_session` | `{}` | `SessionInfo` |
| `x_change_password` | `new_password`、`current_password?` | `SessionCredential` |
| `x_logout` | `{}` | `null` |
| `x_get_config` | `{}` | `ConfigView` |
| `x_set_config` | `config`、`revision` | `ConfigView` |
| `x_get_discovery` | `{}` | `DiscoveryView`，只读取缓存 |
| `x_refresh_discovery` | `{}` | `DiscoveryView`，等待本次扫描结束 |
| `x_activate_account` | `hwnd`、`revision` | `ConfigView`，读取所选窗口资料并完成绑定核验 |

`ConfigView` 包含 `config`、`revision`、`applied_revision`、`accounts`、`activated_accounts`、`errors` 和 `webui`。`webui` 返回当前实际 `host / port / url`、是否等待重启的 `restart_required` 和端口是否由环境覆盖的 `port_overridden`。保存时传回读取到的 `revision`，处理结果按[配置应用状态](configuration.md#修改与热更新)判断。

`DiscoveryView` 包含 `state`（`idle / scanning / ready / failed`）、`session_id`、Unix 秒时间 `scanned_at`、`windows` 及整体 `error`。`windows` 的每项为 `DiscoveredProcess`，只包含 `hwnd / pid / process_name`。探查通过 Win32 / X11 枚举进程窗口，不读取 QQ 号、昵称或头像。整体失败时返回空列表和具体原因。探查操作需要完整登录权限，首次改密前无法调用。

`x_activate_account` 核对缓存中的窗口与当前进程，只对所选窗口读取账号资料。QQ 号、昵称、头像完整且绑定成功后，`ConfigView.activated_accounts` 返回该账号的 `DiscoveredWindow`：`hwnd / pid / nickname / account_id / avatar / available / error`。`avatar` 为原生头像控件的 PNG data URL，资料只缓存在内存；普通刷新复用缓存，进程窗口消失或变化时移除缓存。资料读取失败时操作返回错误，修复窗口状态后可重新连接。

账号连接页进入时调用一次刷新，手动刷新仍然可用；后端启动不扫描。`x_activate_account` 仅接受本次进程缓存中的窗口，更新已有账号窗口时保留网络和监听配置。服务重启后需重新连接以建立资料缓存；探查整体失败时清空激活列表，已有配置保留。

管理 HTTP 的读取与保存直接返回 `ConfigView`，WS 将它放入回执的 `data`。登录、会话和配置动作独立于账号动作目录。

## 错误与执行语义

| retcode | 含义 | 后续处理 |
| --- | --- | --- |
| 0 | 成功，结果符合动作返回类型 | 按结果中的范围和核验字段使用数据 |
| 100 | 未绑定、控件不可用、队列已满或运行错误 | 查看 `message` 与运行状态 |
| 101 | 已提交但结果未知，或写操作回执校验失败 | 查询 QQ 的实际状态后再决定是否重试 |
| 1400 | 参数或目标约束不满足 | 修正请求参数 |
| 1401 | 登录失败或会话失效 | 使用密码重新登录 |
| 1403 | 首次改密尚未完成 | 先设置新密码 |
| 1429 | 登录尝试过多 | 按回执提示的剩余时间重试 |
| 1404 | 未实现的动作、消息形态或功能分支 | 查阅能力矩阵与适配范围 |

HTTP 认证失败返回 401，参数模型校验失败返回 422，管理配置的保存冲突或端口约束错误返回 409。对应的 WS 配置错误通过 `retcode: 1400` 返回。

结果未知时，`data` 为 `{"retryable":false,"outcome":"unknown"}`。超时或取消等待后，已提交的原生操作继续完成。关闭和重配置等待在途动作；写操作由调用方确认实际结果后决定是否再次提交。

同账号业务动作串行执行，跨账号 GUI 操作受共享桌面锁约束。每个账号最多接受 64 个尚未完成的网络调用，WS 业务消息大小上限为 1 MiB。每个事件订阅者的缓冲为 128 条，溢出时以 1013 关闭连接，断线事件不补发。

## 类型与 OpenAPI

`src/quitbot_backend/contracts.py` 定义账号动作的请求和返回类型，`management.py` 定义管理模型。输入会拒绝未声明字段，布尔参数须使用 JSON 布尔值。消息、文件、成员和公告等结果有明确类型，客户端原文由专用文本或 JSON 字段保留。

OpenAPI 3.1 描述 HTTP 路由，正反向 WS 复用同一套参数与返回模型。`x-websocket`、`x-websocket-action`、`x-mutating` 扩展记录调用关联和写操作标记，`x-websocket.reverse` 描述反向握手与重连规则。

在项目根目录生成协议文件：

```powershell
uv run python -m quitbot_backend.export_schema
```

输出为 `protocol/openapi.json`、`protocol/actions.json`、`protocol/management.openapi.json`，并更新能力矩阵中的动作表。

在 `frontend/` 生成 TypeScript 类型：

```powershell
npm run types
```

类型文件为 `src/generated/api.ts` 和 `src/generated/management.ts`。运行中的 Swagger UI 使用相同契约，并提供 Bearer 认证入口。
