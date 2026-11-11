import { useEffect, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Cable,
  Globe,
  Plus,
  Radio,
  RefreshCw,
  Search,
  Settings2,
  Trash2,
} from "lucide-react";
import type { components } from "./generated/management";
import { listenerLabel, reverseLabel } from "./types";
import { Badge, Empty, Modal, Select } from "./ui";
import ConversationDiscovery from "./ConversationDiscovery";

type Account = components["schemas"]["AccountConfig"];
type ConfigView = components["schemas"]["ConfigView"];
type Config = components["schemas"]["ServiceConfig"];
type Listener = components["schemas"]["ListenerConfig"];
type Reverse = components["schemas"]["ReverseWSConfig"];
type Channel = "http" | "forward_ws" | "reverse_ws";
export type AccountAction =
  | "x_connect"
  | "x_disconnect"
  | "x_start_listener"
  | "x_stop_listener"
  | "x_get_runtime_status";
const channels = [
  {
    id: "http",
    name: "HTTP",
    icon: Globe,
    detail: "应用通过 HTTP 请求调用接口。",
  },
  {
    id: "forward_ws",
    name: "正向 WebSocket",
    icon: Cable,
    detail: "应用连接此账号，调用接口并接收事件。",
  },
  {
    id: "reverse_ws",
    name: "反向 WebSocket",
    icon: Radio,
    detail: "此账号主动连接应用的 WebSocket 服务。",
  },
] as const;
const reverseDefaults: Reverse = {
  enabled: false,
  url: "",
  role: "Universal",
  access_token: "",
  reconnect_interval: 3,
};
const listenerDefaults = (port: number): Listener => ({
  enabled: false,
  host: "127.0.0.1",
  port,
});
const address = (scheme: string, endpoint: Listener) =>
  `${scheme}://${endpoint.host?.includes(":") ? `[${endpoint.host}]` : endpoint.host}:${endpoint.port}${scheme === "ws" ? "/ws" : ""}`;

export default function AccountSettings({
  config,
  activeAccountId,
  accessToken,
  view,
  busy,
  onEdit,
  onConnect,
  onAction,
}: {
  config: Config;
  activeAccountId?: string;
  accessToken: string;
  view: ConfigView;
  busy: boolean;
  onEdit: (accountId: string, patch: Partial<Account>) => void;
  onConnect: () => void;
  onAction: (accountId: string, action: AccountAction) => void;
}) {
  const [selected, setSelected] = useState(activeAccountId || "");
  const [picker, setPicker] = useState(false);
  const [editing, setEditing] = useState<Channel>();
  const [discovering, setDiscovering] = useState(false);
  useEffect(() => {
    if (activeAccountId) setSelected(activeAccountId);
    setPicker(false);
    setEditing(undefined);
    setDiscovering(false);
  }, [activeAccountId]);
  const identities = view.activated_accounts.filter(
    (identity) =>
      identity.available &&
      config.accounts?.some(
        (account) => account.account_id === identity.account_id,
      ),
  );
  const identity =
    identities.find((item) => item.account_id === selected) || identities[0];
  const account = config.accounts?.find(
    (item) => item.account_id === identity?.account_id,
  );
  const runtime = view.accounts.find(
    (item) => item.account_id === account?.account_id,
  )?.status;
  if (!account || !identity)
    return (
      <section className="card">
        <Empty title="先连接一个 QQ 账号">
          账号连接页会读取 QQ 号、头像与昵称，连接成功后在这里分别配置。
        </Empty>
        <button className="primary" onClick={onConnect}>
          前往账号连接 <ArrowRight size={15} />
        </button>
      </section>
    );
  const edit = (patch: Partial<Account>) => onEdit(account.account_id, patch);
  const http = { ...listenerDefaults(6700), ...account.http };
  const forward = { ...listenerDefaults(6701), ...account.forward_ws };
  const reverse = { ...reverseDefaults, ...account.reverse_ws };
  const enabled = channels.filter((item) => account[item.id]?.enabled);
  const hasChanges =
    JSON.stringify(account) !==
    JSON.stringify(
      view.config.accounts?.find((item) => item.account_id === account.account_id),
    );
  const listening = runtime?.listening || runtime?.listener_state === "starting";

  return (
    <div className="account-settings-layout">
      <aside className="card account-list" aria-label="已激活账号">
        <div className="account-list-heading">
          <span>已激活账号</span>
          <Badge>{identities.length}</Badge>
        </div>
        {identities.map((item) => (
          <button
            key={item.account_id}
            aria-current={
              item.account_id === account.account_id ? "true" : undefined
            }
            className={`account-list-item ${item.account_id === account.account_id ? "selected" : ""}`}
            disabled={busy}
            onClick={() => {
              setSelected(item.account_id!);
              setPicker(false);
              setEditing(undefined);
            }}
          >
            <img className="qq-avatar" src={item.avatar!} alt="" />
            <span>
              <strong>{item.nickname}</strong>
              <small>{item.account_id}</small>
            </span>
            <span className="status-dot online" />
          </button>
        ))}
        <button className="text-button account-list-add" onClick={onConnect}>
          <Plus size={15} />
          连接其他账号
        </button>
      </aside>
      <div className="account-settings-content">
        <section className="card settings-card">
          <div className="account-settings-heading">
            <img
              className="qq-avatar large"
              src={identity.avatar!}
              alt={`${identity.nickname}的头像`}
            />
            <div>
              <h2>{identity.nickname}</h2>
              <p>QQ {identity.account_id}</p>
            </div>
            <Badge tone="success">已激活</Badge>
          </div>
          <div className="account-runtime" aria-label="账号运行控制">
            <div className="card-heading">
              <h3>运行状态</h3>
              <button
                className="icon-button"
                title="刷新运行状态"
                aria-label="刷新运行状态"
                disabled={busy}
                onClick={() =>
                  onAction(account.account_id, "x_get_runtime_status")
                }
              >
                <RefreshCw size={16} />
              </button>
            </div>
            <div className="account-runtime-status">
              <Badge tone={runtime ? "success" : "danger"}>
                {runtime ? "账号服务就绪" : "账号服务不可用"}
              </Badge>
              <Badge tone={runtime?.connected ? "success" : ""}>
                {runtime?.connected ? "QQ 已绑定" : "QQ 未绑定"}
              </Badge>
              <Badge
                tone={runtime?.listening ? "success" : runtime?.listener_state === "failed" ? "danger" : ""}
              >
                {listenerLabel(runtime)}
              </Badge>
            </div>
            <div className="card-actions">
              <button
                disabled={busy || !runtime}
                onClick={() =>
                  onAction(account.account_id, runtime?.connected ? "x_disconnect" : "x_connect")
                }
              >
                <Cable size={16} />
                {runtime?.connected ? "解除绑定" : "绑定 QQ"}
              </button>
              <button
                className={listening ? "" : "primary"}
                disabled={
                  busy || !runtime ||
                  (!listening && (!runtime.connected || !runtime.targets.length || hasChanges))
                }
                onClick={() =>
                  onAction(account.account_id, listening ? "x_stop_listener" : "x_start_listener")
                }
              >
                <Radio size={16} />
                {listening ? "停止监听" : "启动监听"}
              </button>
            </div>
            {!listening && (
              <p className="muted">
                {hasChanges
                  ? "请先保存配置，再启动监听。"
                  : !runtime?.targets.length
                    ? "请先在下方添加监听会话并保存。"
                    : !runtime.connected
                      ? "绑定 QQ 后即可启动监听。"
                      : "启动后按已保存的范围监听会话。"}
              </p>
            )}
            {runtime?.last_error && (
              <p className="inline-error" role="alert">
                {runtime.last_error}
              </p>
            )}
          </div>
          <div className="settings-fields">
            <label className="field">
              显示名称
              <input
                value={account.label || ""}
                disabled={busy}
                onChange={(e) => edit({ label: e.target.value })}
              />
            </label>
            <label className="field">
              已绑定窗口
              <input
                readOnly
                className="mono"
                value={`HWND ${account.hwnd || identity.hwnd}`}
              />
            </label>
            <label className="field">
              监听方式
              <Select
                label="监听方式"
                value={account.mode || "detached"}
                disabled={busy}
                onChange={(value) =>
                  edit({
                    mode: value as Account["mode"],
                    ...(value === "switch" ? { refresh_detached: false } : {}),
                  })
                }
                options={[
                  { value: "detached", label: "独立聊天窗口" },
                  { value: "switch", label: "切换会话窗口" },
                ]}
              />
            </label>
            <label className="field">
              轮询间隔（秒）
              <input
                type="number"
                min="0.2"
                step="0.1"
                value={account.interval ?? 1}
                disabled={busy}
                onChange={(e) => edit({ interval: Number(e.target.value) })}
              />
            </label>
          </div>
          <div className="settings-switches">
            {(
              [
                [
                  "auto_connect",
                  "启动时自动绑定 QQ",
                  "监听通过本页的运行控制或接口启动。",
                ],
                [
                  "activities",
                  "观察请求与通知",
                  "监听时读取请求卡片和系统消息。",
                ],
                ["track_roster", "观察群成员变化", "比较已加载的群成员列表。"],
              ] as const
            ).map(([key, title, help]) => (
              <label className="setting-switch" key={key}>
                <span>
                  <strong>{title}</strong>
                  <small>{help}</small>
                </span>
                <input
                  type="checkbox"
                  role="switch"
                  checked={!!account[key]}
                  disabled={busy}
                  onChange={(e) => edit({ [key]: e.target.checked })}
                />
              </label>
            ))}
          </div>
          <p className="muted">成员身份映射在「其他设置 → 高级配置」维护。</p>
        </section>
        <section className="card settings-card">
          <div className="card-heading">
            <div>
              <h2>监听范围</h2>
              <p>选择需要监听的群聊或私聊，保存后在本页启动监听。</p>
            </div>
            <div className="listener-target-actions">
              <button disabled={busy} onClick={() => setDiscovering(true)}>
                <Search size={16} /> 探查会话
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  edit({
                    targets: [
                      ...(account.targets || []),
                      { id: "", name: "", kind: "group" },
                    ],
                  })
                }
              >
                <Plus size={16} /> 手动添加
              </button>
            </div>
          </div>
          {!account.targets?.length && (
            <p className="muted">
              点击「探查会话」后勾选群聊或私聊，也可手动填写号码与名称。
            </p>
          )}
          {account.targets?.map((target, index) => (
            <div className="listener-target-row" key={index}>
              <label className="field">
                会话类型
                <Select
                  label={`会话类型 ${index + 1}`}
                  value={target.kind || "group"}
                  disabled={busy}
                  options={[
                    { value: "group", label: "群聊" },
                    { value: "private", label: "私聊" },
                  ]}
                  onChange={(kind) =>
                    edit({
                      targets: account.targets!.map((item, i) =>
                        i === index
                          ? { ...item, kind: kind as "group" | "private" }
                          : item,
                      ),
                    })
                  }
                />
              </label>
              <label className="field">
                {target.kind === "private" ? "QQ 号" : "群号"}
                <input
                  aria-label={`会话号码 ${index + 1}`}
                  inputMode="numeric"
                  value={target.id}
                  disabled={busy}
                  onChange={(event) =>
                    edit({
                      targets: account.targets!.map((item, i) =>
                        i === index
                          ? { ...item, id: event.target.value }
                          : item,
                      ),
                    })
                  }
                />
              </label>
              <label className="field">
                QQ 中的会话名称
                <input
                  aria-label={`会话名称 ${index + 1}`}
                  value={target.name}
                  disabled={busy}
                  onChange={(event) =>
                    edit({
                      targets: account.targets!.map((item, i) =>
                        i === index
                          ? { ...item, name: event.target.value }
                          : item,
                      ),
                    })
                  }
                />
              </label>
              <button
                className="icon-button"
                aria-label={`移除监听会话 ${index + 1}`}
                disabled={busy}
                onClick={() =>
                  edit({
                    targets: account.targets!.filter((_, i) => i !== index),
                    member_bindings: account.member_bindings?.filter(
                      (item) => item.group_id !== target.id,
                    ),
                  })
                }
              >
                <Trash2 size={16} />
              </button>
            </div>
          ))}
        </section>
        {discovering && (
          <ConversationDiscovery
            key={account.account_id}
            accountId={account.account_id}
            accessToken={accessToken}
            targets={(account.targets || []).map((item) => ({
              ...item,
              kind: item.kind || "group",
            }))}
            onClose={() => setDiscovering(false)}
            onAdd={(found) => {
              const targets = [...(account.targets || [])];
              for (const target of found) {
                const index = targets.findIndex(
                  (item) => item.id === target.id,
                );
                if (index < 0) targets.push(target);
                else targets[index] = target;
              }
              edit({ targets });
            }}
          />
        )}
        <section className="card settings-card">
          <div className="card-heading">
            <div>
              <h2>网络连接</h2>
              <p>HTTP、正向 WS 与反向 WS 独立配置，使用同一账号。</p>
            </div>
            <button disabled={busy} onClick={() => setPicker(true)}>
              <Plus size={16} />
              添加连接
            </button>
          </div>
          {!enabled.length && (
            <div className="network-empty">
              <Cable size={26} />
              <strong>尚未启用对外连接</strong>
              <p>选择需要的连接方式。控制台可继续操作此账号。</p>
            </div>
          )}
          <div className="network-list">
            {enabled.map((item) => (
              <button
                className="network-summary"
                key={item.id}
                disabled={busy}
                onClick={() => setEditing(item.id)}
              >
                <span className="tile-icon purple">
                  <item.icon size={20} />
                </span>
                <span>
                  <strong>{item.name}</strong>
                  <small className="mono">
                    {item.id === "reverse_ws"
                      ? reverse.url
                      : address(
                          item.id === "http" ? "http" : "ws",
                          item.id === "http" ? http : forward,
                        )}
                  </small>
                </span>
                <Badge
                  tone={
                    item.id === "reverse_ws" &&
                    runtime?.reverse_ws?.state !== "connected"
                      ? ""
                      : "success"
                  }
                >
                  {item.id === "reverse_ws"
                    ? reverseLabel(runtime?.reverse_ws)
                    : "已启用"}
                </Badge>
                <Settings2 size={16} />
              </button>
            ))}
          </div>
          {runtime?.reverse_ws?.last_error && (
            <p className="inline-error">{runtime.reverse_ws?.last_error}</p>
          )}
        </section>
      </div>
      {picker && (
        <Modal title="选择连接方式" onClose={() => setPicker(false)}>
          <p className="muted">为 {identity.nickname} 添加或编辑连接。</p>
          <div className="transport-choices">
            {channels.map((item) => (
              <button
                className="transport-choice"
                key={item.id}
                onClick={() => {
                  setPicker(false);
                  setEditing(item.id);
                }}
              >
                <span className="tile-icon purple">
                  <item.icon size={25} />
                </span>
                <strong>{item.name}</strong>
                <span>{item.detail}</span>
                <Badge>
                  {account[item.id]?.enabled ? "编辑连接" : "添加连接"}
                </Badge>
              </button>
            ))}
          </div>
        </Modal>
      )}
      {editing && (
        <NetworkEditor
          key={`${account.account_id}:${editing}`}
          type={editing}
          account={account}
          config={config}
          onClose={() => setEditing(undefined)}
          onBack={() => {
            setEditing(undefined);
            setPicker(true);
          }}
          onSave={(patch) => {
            edit(patch);
            setEditing(undefined);
          }}
        />
      )}
    </div>
  );
}

function NetworkEditor({
  type,
  account,
  config,
  onClose,
  onBack,
  onSave,
}: {
  type: Channel;
  account: Account;
  config: Config;
  onClose: () => void;
  onBack: () => void;
  onSave: (patch: Partial<Account>) => void;
}) {
  const used = new Set([
    config.webui?.port,
    ...config.accounts!.flatMap((item) =>
      [item.http, item.forward_ws]
        .filter((endpoint) => endpoint?.enabled)
        .map((endpoint) => endpoint!.port),
    ),
  ]);
  let port = type === "http" ? 6700 : 6701;
  while (used.has(port) && port < 65535) port++;
  const previous = type === "http" ? account.http : account.forward_ws;
  const [listener, setListener] = useState<Listener>(() => ({
    ...listenerDefaults(port),
    ...previous,
    enabled: true,
    ...(!previous?.enabled && used.has(previous?.port) ? { port } : {}),
  }));
  const [reverse, setReverse] = useState<Reverse>(() => ({
    ...reverseDefaults,
    ...account.reverse_ws,
    enabled: true,
  }));
  const [error, setError] = useState("");
  const title = channels.find((item) => item.id === type)!.name;
  const isReverse = type === "reverse_ws";
  return (
    <Modal title={`配置 ${title}`} onClose={onClose}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (
            isReverse &&
            reverse.enabled &&
            !/^wss?:\/\//.test(reverse.url || "")
          ) {
            setError("请填写 ws:// 或 wss:// 接收地址");
            return;
          }
          onSave(isReverse ? { reverse_ws: reverse } : { [type]: listener });
        }}
      >
        <label className="setting-switch">
          <span>
            <strong>启用此连接</strong>
            <small>此设置仅影响 {title}。</small>
          </span>
          <input
            type="checkbox"
            role="switch"
            checked={isReverse ? reverse.enabled : listener.enabled}
            onChange={(e) =>
              isReverse
                ? setReverse({ ...reverse, enabled: e.target.checked })
                : setListener({ ...listener, enabled: e.target.checked })
            }
          />
        </label>
        {isReverse ? (
          <>
            <label className="field">
              接收地址
              <input
                placeholder="ws://127.0.0.1:8080/onebot/v11/ws"
                value={reverse.url}
                required={reverse.enabled}
                onChange={(e) =>
                  setReverse({ ...reverse, url: e.target.value })
                }
              />
            </label>
            <label className="field">
              接收端令牌
              <input
                type="password"
                autoComplete="off"
                value={reverse.access_token}
                onChange={(e) =>
                  setReverse({ ...reverse, access_token: e.target.value })
                }
              />
            </label>
            <div className="field">
              <span>传输内容</span>
              <div
                className="segmented-options"
                role="radiogroup"
                aria-label="反向 WS 传输内容"
              >
                {(
                  [
                    ["Universal", "接口与事件"],
                    ["API", "仅接口"],
                    ["Event", "仅事件"],
                  ] as const
                ).map(([value, label]) => (
                  <button
                    type="button"
                    role="radio"
                    aria-checked={reverse.role === value}
                    className={reverse.role === value ? "selected" : ""}
                    key={value}
                    onClick={() => setReverse({ ...reverse, role: value })}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <label className="field">
              重连间隔（秒）
              <input
                type="number"
                min="0.2"
                max="300"
                step="0.1"
                value={reverse.reconnect_interval}
                onChange={(e) =>
                  setReverse({
                    ...reverse,
                    reconnect_interval: Number(e.target.value),
                  })
                }
              />
            </label>
          </>
        ) : (
          <div className="settings-fields network-editor-fields">
            <label className="field">
              监听地址
              <input
                required
                placeholder="127.0.0.1"
                value={listener.host}
                onChange={(e) =>
                  setListener({ ...listener, host: e.target.value })
                }
              />
            </label>
            <label className="field">
              端口
              <input
                required
                type="number"
                min="1024"
                max="65535"
                value={listener.port}
                onChange={(e) =>
                  setListener({ ...listener, port: Number(e.target.value) })
                }
              />
            </label>
          </div>
        )}
        {!isReverse && (
          <p className="settings-hint">
            可填写本机 IP、主机名或 0.0.0.0。
            {type === "http"
              ? "HTTP 使用 /api/{action} 调用。"
              : "正向 WS 使用 /ws 连接。"}
          </p>
        )}
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" onClick={onBack}>
            <ArrowLeft size={15} />
            连接方式
          </button>
          <button className="primary" type="submit">
            确认配置
          </button>
        </div>
        <p className="muted">确认后，点击页面上的「保存并应用」使配置生效。</p>
      </form>
    </Modal>
  );
}
