# 部署、配置与运行

## 运行环境

准备 Windows 或 Linux X11、官方 QQ 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。后端与 QQ 位于同一个图形会话。安装脚本检查 `uv` 是否可用，Python 由 `uv sync` 按项目要求自动准备。Release 的源码包包含 CI 预构建的控制台，部署与运行无需 Node.js / npm。

Ubuntu 24.04 先安装无障碍与图形会话的系统依赖：

```bash
sudo apt-get install at-spi2-core gir1.2-atspi-2.0 libgirepository1.0-dev libcairo2-dev python3-dev pkg-config xvfb openbox dbus-x11 xauth x11-utils xdotool xclip
```

需要隔离自动化和日常桌面时，可自行使用 Anode 或其他独立 Windows 会话。Anode 为可选环境，项目不附带其安装包。QQ 独立聊天窗口共享所在桌面的输入焦点。

## 使用安装脚本

在准备存放项目的位置运行一行命令。脚本位于仓库的 `scripts/`，下载的是[最新正式 Release](https://github.com/GEYUANwuqi/QuitBot/releases/latest) 中包含预构建控制台的源码包。

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/GEYUANwuqi/QuitBot/master/scripts/install.ps1 | iex
```

Linux Bash：

```bash
curl -fsSL https://raw.githubusercontent.com/GEYUANwuqi/QuitBot/master/scripts/install.sh | bash
```

脚本执行流程：

1. 检查 `uv`，缺少时直接报错，并给出安装入口。
2. 解析最新正式版，下载对应的 `quitbot-<版本>.tar.gz` 源码包。
3. 在当前目录创建 `QuitBot_<实际版本>`，例如 `QuitBot_0.13.0`，解压前后端源码、锁文件、文档和预构建控制台。
4. 在项目目录执行 `uv sync --locked --no-dev`，由 uv 准备 Python 和运行依赖。
5. 输出项目路径与启动命令。

同名目录已存在时脚本停止，保留已有配置和文件。依赖安装失败时保留解压目录，按错误提示补齐条件后，可进入该目录重新执行 `uv sync --locked --no-dev`。后续启动使用已安装版本。

也可先下载或克隆仓库，在准备存放项目的位置执行本地的 `scripts/install.sh` 或 `scripts/install.ps1`。安装位置取决于执行时的当前目录，而非脚本所在目录。

### Windows 或已有 X11 桌面

安装完成后，按脚本输出进入 `QuitBot_<实际版本>` 目录。在同一图形会话中登录官方 QQ，再启动后端：

```sh
uv run --locked --no-dev python -m quitbot_backend
```

Linux 的 QQ 与后端需共享 `DISPLAY`、`XAUTHORITY` 和 `DBUS_SESSION_BUS_ADDRESS`。QQ 启动前启用 `org.a11y.Status` 的 `IsEnabled`、`ScreenReaderEnabled`，并使用 `--force-renderer-accessibility`。

### 纯无头 Linux

进入安装目录，以普通 Linux 用户运行下面的入口，它同时启动 QQ 和后端：

```bash
uv run --locked --no-dev python -m quitbot_backend.desktop --display :99 --qq /opt/QQ/qq
```

按实际安装位置修改 QQ 路径。入口创建独立 D-Bus、带认证的 Xvfb 和 Openbox，并自动启用辅助技术。通过按需连接的桌面查看工具登录 QQ，查看连接断开后图形会话继续运行。查看工具绑定回环地址并通过 SSH 转发访问；长期运行时由 systemd 监督此入口。多个实例分别使用独立用户 / 图形会话、显示编号和配置位置，详情见 [README](../README.md#快速开始)。

## 首次启动

启动后打开终端显示的控制台地址，默认是 `http://127.0.0.1:6699/`，使用临时密码登录并完成改密。

运行配置自动生成，通过控制台或文件编辑维护。

| 文件 | 用途 |
| --- | --- |
| `config/accounts.json` | WebUI 与各 QQ 账号配置，首次创建时账号列表为空 |
| `config/security.json` | 密码的随机盐、摘要和首次改密状态 |
| `config/state/<account_id>/messages.sqlite3` | 按账号隔离的本地消息 ID 映射，按需创建 |

账号配置保留已有值。`config/` 的运行数据由 Git 忽略并从构建产物中排除。管理服务默认监听 `127.0.0.1:6699`，账号列表为空时也可连接；每个账号独立配置 HTTP、正向 WS 和反向 WS。

## 登录、密码与会话

1. 首次启动时，终端显示随机临时密码。打开本机 [QuitBot](http://127.0.0.1:6699/) 进入登录页，页面自动连接本机服务。
2. 输入临时密码后进入强制改密页。设置 8–128 个字符的新密码，完成后才能读取配置和操作 QQ。
3. 后续使用新密码登录。单次登录默认有效 **4 小时**，可通过 `webui.session_hours` 配置为 0.1–720 小时。修改对后续新登录生效，已有会话保留原到期时间；刷新页面和发起请求均不续期。
4. 到期后返回登录页，后端同时拒绝过期的 HTTP / WS 凭证并关闭已建立的正向 WS。退出登录会撤销当前会话；改密会撤销全部已有会话，并签发新的当前会话。

密码使用随机盐和 PBKDF2-HMAC-SHA256 摘要保存。临时密码仅在生成时显示于终端，密码明文不写入配置。浏览器仅在当前标签页的 `sessionStorage` 保存会话凭证及到期时间；密码输入不会持久化。后端重启后需要重新登录。

首次改密前，已登录会话只能查询自身状态、修改密码或退出。密码输错默认连续 5 次后暂停登录尝试 30 秒，这两项可在「其他设置 → WebUI 与登录」调整并热更新。

忘记密码时，停止后端，将 `config/security.json` 移到本机备份目录，再启动并使用新生成的临时密码。保留 `accounts.json` 和 `state/`，账号配置及消息索引继续使用。

退出控制台或登录到期只关闭客户端连接，后端已运行的 QQ 监听和反向连接继续运行。停止监听、解除绑定仍通过对应账号操作完成。

## 修改与热更新

在「账号连接」探查并连接 QQ，在「账号设置」左侧选择已激活账号。右侧管理账号、监听范围及网络连接；监听范围可探查会话后勾选，或手动填写号码和对应名称。点击「添加连接」选择 HTTP、正向 WS 或反向 WS。在「其他设置 → 高级配置」编辑成员身份映射等细项。保存时校验字段类型、账号 / 端口唯一性和会话关系，并原子写回文件。

读取结果中的 `revision` 是本次保存的依据。如果文件已被其他窗口或编辑器修改，保存会报告冲突；重新读取并合并修改后再保存。

运行中每秒检查文件变化。有效修改只重启受影响的账号服务；新增账号启动新服务，移除账号停止对应服务。重建前等待已开始的 UI 操作完成，再恢复原本的绑定及正在启动或运行的监听。监听范围清空时停止监听。恢复失败会记录应用错误并尝试恢复原配置。其他账号及管理连接继续运行。

应用结果按以下字段判断：

| 字段 | 含义 |
| --- | --- |
| `revision` | 当前期望配置的版本 |
| `applied_revision` | 最后一次完整应用的配置版本 |
| `errors` | 配置校验或各账号应用失败的原因 |
| `accounts` | 当前运行的账号与状态 |
| `activated_accounts` | 连接时已完整读取资料并核验的账号，含缓存的 QQ 号、昵称与头像 |

`revision == applied_revision` 且 `errors` 为空表示整份配置已应用。WebUI 地址或端口等待重启时，`webui.restart_required` 为 `true`，`applied_revision` 保留此前版本；账号设置及登录保护仍可热更新。格式错误的文件会保留当前有效状态；账号端口占用等应用失败会尝试恢复该账号的原服务。修正配置或重新保存可再次应用。

账号服务重建会以 `1012` 关闭账号 WS。控制台等待账号服务就绪后自动恢复连接，保留当前页面和操作记录，无需重新探查；第三方正向客户端自行重连，启用的反向连接自动重建。重连只恢复认证和状态读取，业务请求不重发。

## QQ 窗口探查

进入「账号连接」页面时主动探查一次当前图形会话内带 QQ 主窗口的进程，缓存进程名、窗口句柄、进程 ID 和探查时间。枚举只读取 Win32 / X11 元数据，不初始化无障碍控件读取器、切换焦点或获取账号资料。后端启动及进入其他页面不触发扫描。

页面读取缓存不会重新探查。打开 QQ 或窗口变化后，点击「刷新探查」。同时发起的刷新共用一次扫描，已退出或无法读取进程信息的窗口跳过。列表同时显示后端的 Windows 会话 ID，使用 Anode 时将 QQ 与后端放在同一子会话。

点击「连接账号」后核对所选窗口的进程，沿用桌面事务锁读取该账号的昵称、QQ 号与头像。读取 QQ 号会打开并关闭本人资料卡，头像从无障碍接口定位的头像控件区域读取。三项资料完整后保存账号和窗口，保留已有监听与网络配置，并在绑定时再次核验 QQ 号。连接成功的账号资料缓存在 `activated_accounts`，供账号设置列表使用。

普通刷新保留仍存在的进程窗口的资料缓存；窗口消失或所属进程变化时隐藏对应账号，其配置保留。在 QQ 中切换登录账号后需重新连接以更新身份资料。新账号默认关闭三种对外连接，控制台通过独立账号通道操作。

## WebUI 字段

`accounts.json` 顶层包含 `webui` 与 `accounts`。WebUI 字段如下：

| 字段 | 默认值 | 生效方式 |
| --- | --- | --- |
| `webui.host` | `127.0.0.1` | 自定义 IP 或主机名，可使用 `0.0.0.0` / `::` 监听所有网卡；保存后重启后端 |
| `webui.port` | `6699` | 1024–65535，与各账号端口分开；保存后重启后端 |
| `webui.login_attempts` | `5` | 连续密码错误阈值，1–20，热更新 |
| `webui.login_retry_seconds` | `30` | 达到阈值后暂停登录的秒数，1–3600，热更新 |
| `webui.session_hours` | `4` | 单次登录时长，0.1–720 小时；热更新后对新登录生效 |

控制台显示当前实际地址和重启提示。改动 WebUI 地址或端口后，保存并重启，再打开新地址。监听所有网卡时，浏览器使用电脑的实际 IP 访问。

## 账号字段

`accounts` 数组的每项配置一个 QQ 账号。

| 字段 | 含义 |
| --- | --- |
| `account_id` | 必填，数字 QQ 号字符串，绑定时核验窗口身份 |
| `label` | 展示名称，默认为空 |
| `http` | HTTP 监听配置：`enabled` 默认 `false`，`host` 默认 `127.0.0.1`，`port` 默认 `6700` |
| `forward_ws` | 正向 WS 监听配置：`enabled` 默认 `false`，`host` 默认 `127.0.0.1`，`port` 默认 `6701` |
| `hwnd` | 可选窗口句柄，绑定时仍核验 QQ 号 |
| `targets` | 操作和监听范围；每项包含数字字符串 `id`、精确名称 `name`、类型 `kind`（`group` / `private`，默认 `group`） |
| `auto_connect` | 默认 `false`；设为 `true` 时启动实例后自动绑定，监听另行启动 |
| `mode` | `detached` / `switch`，默认 `detached` |
| `interval` | 轮询间隔，默认 1 秒、最小 0.2 秒；实际执行受原生控件队列影响 |
| `refresh_detached` | 默认 `false`，仅 `detached` 可用，刷新可能激活窗口 |
| `activities` | 默认 `false`；监听时观察请求卡片和系统消息 |
| `request_kinds` | 请求类别，默认包含 `group` 和 `friend` |
| `track_roster` | 默认 `false`；观察已加载成员列表变化 |
| `resolve_members` | 默认 `false`；读取可见资料的数字身份，会增加 UI 操作 |
| `member_bindings` | 每项包含 `group_id`、精确名称 `name`、`user_id`；所引用的群须在 `targets` 中 |
| `origins` | 额外允许的浏览器来源，默认本机 `localhost` / `127.0.0.1` 的 5173 和 4173 端口；本机控制台所在端口自动允许 |
| `reverse_ws` | 账号的反向 WS 配置，默认关闭，字段见下表 |

`detached` 通过独立聊天窗口轮询，`switch` 通过切换会话轮询。详情见[架构与监听](architecture.md#监听与事件)。

`targets` 可在「账号设置 → 监听范围」通过会话探查勾选生成，或手动填写。探查读取已加载的最近会话名称，所选会话再从 QQ 的复制号码菜单读取身份；保存配置才更新监听范围。账号进程探查仍只读取窗口元数据。

绑定、解除绑定、启动和停止监听在「账号设置」中按左侧所选账号执行，属于临时运行状态。连接成功后自动进入对应账号设置，概览只展示状态。设置持久化由配置保存完成；进程重新启动时根据 `auto_connect` 决定是否绑定，监听需另行启动。

## HTTP 与正向 WS 配置

两种连接分别使用账号的 `http` 和 `forward_ws` 字段，可单独添加、启用或关闭。监听地址支持 IP、主机名与通配地址，端口范围为 1024–65535。不同账号及 WebUI 使用不同端口；同一账号的 HTTP 与正向 WS 设置为相同地址、端口时共用监听端点，其他情况下分别监听。仅启用 HTTP 的端点拒绝 WS 握手，仅启用正向 WS 的端点不提供 HTTP 动作与文档。

控制台的 `/accounts/{account_id}/ws` 通道沿用登录会话，与对外 HTTP、正向 WS 和反向 WS 配置独立。

## 反向 WS 配置

每个账号可以配置一个反向接收端，并按需独立启用 HTTP 与正向 WS。在「账号设置」点击「添加连接 → 反向 WebSocket」，填写参数后确认，再「保存并应用」。

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `enabled` | `false` | 是否主动连接接收端 |
| `url` | 空 | `ws://` / `wss://` 接收地址；启用时必填。地址不含凭证、查询参数或片段，令牌单独填写 |
| `role` | `Universal` | `Universal` 收发动作与事件，`API` 仅接收动作，`Event` 仅推送事件 |
| `access_token` | 空 | 接收端设置的访问令牌，可选；填写后通过 Bearer 请求头发送 |
| `reconnect_interval` | `3` | 重连间隔，单位秒，范围为 0.2–300 |

接收端令牌保存在 `accounts.json`，用于外连握手；与 `security.json` 中的登录密码摘要分别管理。运行状态、日志和事件不返回该令牌。

URL、令牌、用途或启用状态变化按账号热更新。该账号先停止反向连接并等待在途操作，再重建服务和连接；其他账号继续运行。关闭反向连接后停止重连，保留配置便于再次启用。

正反向共用账号的 `targets`、绑定状态和监听源。启用反向连接后，可通过接收端调用 `x_connect` 与 `x_start_listener`；连接建立本身不会启动 QQ 监听。

## 配置位置与数据

`QUITBOT_CONFIG` 可指定其他账号配置文件，密码文件和 `state/` 位于所选文件旁。需要保留消息 ID 映射时，应同时保留对应账号的状态目录；消息 ID 的使用范围见 [OneBot 适配](onebot11.md#消息标识与时间)。

`QUITBOT_MANAGEMENT_PORT` 可在启动时覆盖配置中的 WebUI 端口，控制台会显示该覆盖状态。通常直接在 WebUI 设置中维护地址与端口。管理服务监听配置的地址，浏览器来源允许本机 5173 / 4173 及同源访问。账号配置中的 `origins` 只作用于账号服务。

## 前端运行

Release 安装后，启动后端即可使用控制台，无需单独启动前端进程。后端从包内 `_webui/` 加载预构建资源，在同一本机地址提供控制台、登录与配置 WS，浏览器直接打开 `http://127.0.0.1:6699/`。

登录后在「账号连接」探查并连接账号，在「账号设置」分别维护配置，在「其他设置」维护 WebUI、登录和完整配置。控制台管理和业务调用均经 WS；Swagger 使用 HTTP API。

## 前端开发

修改前端或从 Git 仓库自行构建控制台时，需要 Node.js 22.12+ / npm。在源码根目录执行：

```sh
uv sync --locked
npm --prefix frontend ci
npm --prefix frontend run build
uv run python -m quitbot_backend
```

前端构建产物写入 `src/quitbot_backend/_webui/`。修改前端源码后重新构建，后端便会提供更新后的控制台。使用 Release 源码包且只修改后端时，可以继续使用包内预构建资源。

开发模式使用 `npm --prefix frontend run dev`，页面位于 `http://127.0.0.1:5173/`，Vite 将 `/ws` 和 `/accounts` 代理到本机后端。默认读取 `QUITBOT_CONFIG` 或 `config/accounts.json` 中的 WebUI 地址与端口，并使用 `QUITBOT_MANAGEMENT_PORT` 覆盖值；调整后重启 Vite。`npm run preview` 使用 4173 端口及相同代理。控制台自动选择同源服务。

## 构建与分发检查

以下流程供自行构建和发布使用，需要 Node.js 22.12+ / npm。在源码根目录执行：

```powershell
uv sync --locked
npm --prefix frontend ci
npm --prefix frontend run build
uv build --out-dir .local/build
uv run python scripts/check_distribution.py .local/build
```

检查目录应只包含当前版本的一份 wheel 和 sdist。两种产物均包含已构建的控制台及其第三方许可声明；sdist 同时包含前端源码，重新构建 Python wheel 时可直接使用随包资源。首次开发安装 `uv sync` 可以在前端构建前运行，正式打包会拒绝缺少控制台资源的状态。

安装 wheel 后，从选定的数据目录执行 `python -m quitbot_backend` 即可运行，运行环境无需 Node.js。默认配置相对于启动目录创建；通过 `QUITBOT_CONFIG` 可固定配置位置。静态资源始终从已安装的包读取。

GitHub Actions 的 Windows CI 在分支 push、pull request 和手动触发时执行锁定依赖安装、Ruff、完整模拟测试、协议与类型一致性检查、前端构建、sdist / wheel 构建和安装验证。安装验证在独立临时环境中检查控制台、JS / CSS / 图标、首次登录改密及退出，不连接真实 QQ。通过的分发包和许可证保留为 CI 附件，运行配置、索引、缓存和本地勘探资料不进入附件。

Linux CI 在 Ubuntu 24.04 安装原生无障碍依赖，执行静态检查和后端回归。Linux 启动器将 QQ、后端和无障碍服务放在同一 D-Bus 与 X11 会话中；多个隔离会话分别使用配置文件、显示编号和管理端口。

### GitHub Releases

[Release 工作流](../.github/workflows/release.yml) 在推送 `v*` 标签时自动运行。标签采用 `v<版本>` 格式，必须与该提交的 `pyproject.toml` 中版本一致。发布前同步修改 `pyproject.toml` 和 `src/quitbot_backend/__init__.py` 中的版本，更新 `uv.lock`，再运行 `uv run python -m quitbot_backend.export_schema` 和 `npm --prefix frontend run types`。前端版本同步维护在 `frontend/package.json` 和 `frontend/package-lock.json`，完成后提交变更。

```bash
quitbot_version=$(uv run python -c 'from quitbot_backend import __version__; print(__version__)')
git tag -a "v$quitbot_version" -m "QuitBot v$quitbot_version"
git push origin "v$quitbot_version"
```

工作流复用 Windows 和 Linux CI，全部检查通过后创建 Release，并自动生成发布说明。附件包含：

- `quitbot-<版本>.tar.gz`：包含前后端源码、文档和已构建控制台的 Python 源码包。
- `quitbot-<版本>-py3-none-any.whl`：Windows / Linux 共用的安装包，包含控制台资源，依赖按平台选择。
- `LICENSE`：项目许可证。
- `THIRD_PARTY_LICENSES.txt`：控制台第三方组件许可声明。

带 `a`、`b`、`rc` 或 `dev` 版本标记的发布自动标为预发布。手动触发时使用标签引用，例如 `gh workflow run release.yml --ref "v$quitbot_version"`；分支引用和版本不匹配的标签会在构建前报错。发布使用 GitHub Actions 自带的 `GITHUB_TOKEN`，无需额外配置发布凭证。
