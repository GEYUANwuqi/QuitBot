# QuitBot

基于 **官方 QQ 原生界面** 的跨平台异步账号服务（Windows UI Automation / Linux AT-SPI），提供每个 QQ 账号独立的正向 / 反向 WebSocket、类型化 HTTP API 和 React 控制台。

消息、群文件、公告和成员管理等操作通过 QQ 原生界面执行。可用范围取决于客户端界面、账号权限和内容加载状态，具体见[能力矩阵](docs/capabilities.md)与 [OneBot v11 适配现状](docs/onebot11.md)。

项目采用[源码可见、非商业使用与禁止再分发许可证](LICENSE)，允许非商业自用和私下修改。商业使用或再分发须取得书面许可，详见[许可说明](docs/licensing.md)。

## 主要能力

- **消息与会话**：群聊 / 私聊文字、本地图片与文件发送，引用、单条转发、@、撤回、精华、贴表情，以及草稿、置顶和免打扰管理。
- **群文件**：目录读取、搜索、排序、上传、下载、重命名、移动、删除，文件夹管理与批量操作。
- **群管理**：公告发布、编辑、删除和置顶，管理员设置、禁言、踢人、入群策略及群资料操作。
- **资料与表情**：好友资料、单次点赞、名片和群二维码分享，收藏表情读取、导入、排序、备注及删除。
- **查询与监听**：已加载消息、历史筛选、日历和图片定位，独立聊天窗口轮询，部分请求与通知事件。

## 快速开始

先安装官方 QQ 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。Python 由 `uv sync` 按项目要求自动准备，Release 自带 CI 预构建的控制台，**无需安装 Node.js 或 npm**。Linux 还需准备[无障碍与 X11 系统依赖](docs/configuration.md#运行环境)。后端与 QQ 运行在同一个图形会话中。

在准备存放项目的位置执行一行安装命令。

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/GEYUANwuqi/QuitBot/master/scripts/install.ps1 | iex
```

Linux Bash：

```bash
curl -fsSL https://raw.githubusercontent.com/GEYUANwuqi/QuitBot/master/scripts/install.sh | bash
```

脚本先检查 `uv`，缺少时直接报错；随后下载最新正式 Release 的预构建源码包，在当前目录创建 **`QuitBot_<实际版本>`**（例如 `QuitBot_0.13.0`），并执行 `uv sync --locked --no-dev`。源码、锁文件和控制台来自同一个版本。同名目录已存在时停止，保留已有文件。

安装完成后，按脚本输出进入项目目录，再选择下面的启动方式。

Windows 或已有 X11 桌面中，登录官方 QQ 后启动后端：

```sh
uv run --locked --no-dev python -m quitbot_backend
```

纯无头 Linux 使用下面的命令启动 QQ 和后端，以普通用户创建独立图形会话：

```bash
uv run --locked --no-dev python -m quitbot_backend.desktop --display :99 --qq /opt/QQ/qq
```

该入口创建独立 D-Bus、带认证的 Xvfb、Openbox 和系统托盘，在启动 QQ 前启用辅助技术，并同时运行后端。登录 QQ 时可按需连接桌面查看工具；关闭查看连接后图形会话继续运行。终端输出 `DISPLAY` 和 `XAUTHORITY`，供查看工具使用。长期运行时将此入口交给 systemd 管理；启动目录和 `QUITBOT_CONFIG` 决定配置位置。每个隔离用户 / 图形会话运行一个后端实例，使用不同显示编号和管理端口。查看工具应绑定回环地址并通过 SSH 转发访问。

已有 X11 桌面可直接运行后端；QQ 与后端需共享 `DISPLAY`、`XAUTHORITY` 和 `DBUS_SESSION_BUS_ADDRESS`。QQ 启动前启用 `org.a11y.Status` 的 `IsEnabled`、`ScreenReaderEnabled`，并使用 `--force-renderer-accessibility`。Linux 文本输入通过剪贴板和 XTEST 完成，会更新该图形会话的剪贴板。

首次运行自动创建 `config/accounts.json` 和 `config/security.json`，终端显示随机生成的临时密码。初始账号列表为空。

打开本机 [QuitBot](http://127.0.0.1:6699/) 后：

1. 在登录页输入终端中的临时密码。
2. 立即设置新密码，完成后进入控制台。单次登录默认有效 4 小时，可在其他设置中调整。
3. 进入「账号连接」，页面自动列出本会话的 QQ 进程窗口。选择「连接账号」后读取该窗口的 QQ 号、头像和昵称，核验成功后连接。后续可手动刷新进程列表。
4. 连接成功后自动进入该 QQ 的账号设置。在「监听范围」点击「探查会话」，勾选并核验需要监听的群聊或私聊；也可手动填写号码与名称。保存后在本页点击「启动监听」。
5. 在「账号设置」点击「添加连接」，分别设置 HTTP、正向 WS 或反向 WS，保存并应用。成员身份映射等细项在「其他设置 → 高级配置」维护。

控制台由后端直接提供，自动连接本机服务。每个 QQ 账号保留独立配置、端口、绑定窗口和监听状态。

安装与运行说明见[部署指南](docs/configuration.md#使用安装脚本)。修改前端或从 Git 源码自行构建时，才需要 Node.js / npm，见[前端开发](docs/configuration.md#前端开发)。配置默认保存在启动目录下的 `config/`，也可用 `QUITBOT_CONFIG` 指定位置。

接入方式分别配置，可同时启用：

- **HTTP**：应用请求账号的 `/api/{action}` 接口。
- **正向 WS**：应用连接账号的 `/ws` 地址，调用接口并接收事件。
- **反向 WS**：QuitBot 主动连接应用的接收地址，断线后自动重连。

控制台使用同源的账号专用 WS 通道，独立于上述对外连接；新连接账号默认不开启对外端口。

反向连接支持 Universal（动作与事件）、API（仅动作）、Event（仅事件），接收端令牌单独配置。详见[反向连接说明](docs/api.md#反向-websocket)。

运行配置、密码摘要和消息索引保存在 `config/`。控制台保存与手动文件修改均支持热更新，只重启受影响的账号服务。完整字段、密码重置和应用结果说明见[配置与运行](docs/configuration.md)。

## 控制台

| 页面 | 功能 |
| --- | --- |
| 登录页 | 密码登录、首次强制改密，会话到期返回登录页 |
| 概览 | 只读展示服务连接、QQ 绑定、监听状态、配置会话和最近调用 |
| 账号连接 | 进入页面探查进程、手动刷新；连接时读取并核验所选账号资料 |
| 账号设置 | 左侧切换已激活账号，右侧绑定 / 解绑、启停监听，维护账号配置及独立 HTTP、正向 WS、反向 WS 设置 |
| 其他设置 | 自定义 WebUI 地址与端口、登录有效期、登录保护、完整配置 |
| 接口调试与实时事件 | 动作检索、表单 / JSON 参数、调用记录、事件筛选及原始数据 |

控制台通过 WS 完成管理和接口调试，支持浅色 / 深色主题、统一下拉选择器及窄屏导航。消息、群文件和公告等业务由 API 调用。操作范围和详细流程见[控制台使用说明](docs/webui.md)。

## API

账号服务提供以下入口，WS 与 HTTP 共用参数和返回类型：

| 入口 | 用途 |
| --- | --- |
| WS `/ws` | 动作调用与事件接收 |
| HTTP `POST /api/{action}` | 类型化动作调用 |
| HTTP `/docs` | Swagger UI |
| HTTP `/openapi.json` | OpenAPI 3.1 文档 |
| WS 动作 `x_get_api_catalog` | 动作清单、参数与返回值的 JSON Schema |

WebUI、正向 WS 和 HTTP 的监听地址与端口均可自定义，默认使用本机回环地址。受保护接口使用登录后取得的会话凭证；反向 WS 连接配置的 `ws://` / `wss://` 接收地址。写操作超时或断线后，应先查询实际结果，再决定是否重试。请求格式、认证流程及错误处理见[接口调用](docs/api.md)。

后端源码位于 `src/quitbot_backend/`，前端位于 `frontend/`，协议文件位于 `protocol/`。结构与运行机制见[架构说明](docs/architecture.md)。

## 可选环境与工具

**Anode 是可选环境。** 为减少自动化与日常桌面的操作冲突，可自行使用 Anode 或其他独立 Windows 会话，把 QQ 和后端一起运行在其中。项目不提供 Anode。QQ 独立聊天窗口适合分别读取会话，输入焦点仍由所在 Windows 桌面共享。

**Spy++ 是开发辅助工具，作用有限。** 它可查看 Windows 窗口层次和窗口消息；现代 QQ 的主要内容位于 Chromium 控件中，聊天信息和业务操作以 UIA 为主。工具可通过 [Visual Studio 官方下载入口](https://visualstudio.microsoft.com/downloads/)获取，使用方法见 [Spy++ 官方说明](https://learn.microsoft.com/en-us/visualstudio/debugger/introducing-spy-increment?view=visualstudio)和[启动说明](https://learn.microsoft.com/en-us/visualstudio/debugger/how-to-start-spy-increment?view=visualstudio)。

**FlaUInspect 是可选的 UIA 开发辅助工具。** 可查看 UIA2 / UIA3 控件树、属性、操作模式及悬停和焦点对应的控件，适合确认定位路径。下载见 [FlaUI 官方仓库](https://github.com/FlaUI/FlaUInspect)与 [Releases](https://github.com/FlaUI/FlaUInspect/releases)。

上述工具均由使用者自行安装。

## 文档

- [文档索引](docs/README.md)
- [已实现能力与完整动作目录](docs/capabilities.md)
- [OneBot v11 适配现状](docs/onebot11.md)
- [接口调用与类型](docs/api.md)
- [部署、配置与运行](docs/configuration.md)
- [控制台使用说明](docs/webui.md)
- [项目架构](docs/architecture.md)
- [许可说明](docs/licensing.md)
