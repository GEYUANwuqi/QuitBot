import { useEffect, useRef, useState } from "react";
import { Check, Globe, KeyRound, RefreshCw, Save } from "lucide-react";
import type { components } from "./generated/management";
import { Connection } from "./transport";
import { accountProfile, format, type Status } from "./types";
import { Badge, Empty } from "./ui";
import AccountSettings, { type AccountAction } from "./AccountSettings";

type ConfigView = components["schemas"]["ConfigView"];
type Config = components["schemas"]["ServiceConfig"];
type Account = components["schemas"]["AccountConfig"];
type WebUI = components["schemas"]["WebUIConfig"];
const webuiDefaults: WebUI = {
  host: "127.0.0.1",
  port: 6699,
  login_attempts: 5,
  login_retry_seconds: 30,
  session_hours: 4,
};

export default function SettingsPage({
  section,
  activeAccountId,
  accessToken,
  management,
  onAccounts,
  onPasswordChange,
  onConnect,
  onRuntimeStatus,
}: {
  section: "accounts" | "other";
  activeAccountId?: string;
  accessToken: string;
  management: Connection;
  onAccounts: (view: ConfigView) => void;
  onPasswordChange: () => void;
  onConnect: () => void;
  onRuntimeStatus: (status: Status) => void;
}) {
  const lock = useRef(false);
  const operation = useRef(0);
  const [tab, setTab] = useState("webui");
  const [view, setView] = useState<ConfigView>();
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  let config: Config | undefined;
  try {
    const value = JSON.parse(draft);
    if (value && Array.isArray(value.accounts)) config = value;
  } catch {
    /* Keep incomplete JSON for editing. */
  }
  const webui = { ...webuiDefaults, ...config?.webui };
  const changed = !!view && draft !== format(view.config);

  function show(next: ConfigView) {
    setView(next);
    setDraft(format(next.config));
    onAccounts(next);
  }
  useEffect(() => {
    let active = true;
    void management
      .callRaw("x_get_config")
      .then((value) => {
        if (active) show(value as ConfigView);
      })
      .catch((cause) => {
        if (active) setError(String(cause));
      });
    return () => {
      active = false;
    };
  }, [management]);

  useEffect(() => {
    if (section !== "accounts") return;
    let active = true;
    let polling = false;
    const timer = window.setInterval(async () => {
      if (lock.current || polling || document.hidden) return;
      const epoch = operation.current;
      polling = true;
      try {
        const next = (await management.callRaw("x_get_config")) as ConfigView;
        if (!active || lock.current || epoch !== operation.current) return;
        // Refresh runtime data without replacing the configuration draft or its revision.
        setView((current) =>
          current && {
            ...current,
            accounts: next.accounts,
            activated_accounts: next.activated_accounts,
          },
        );
        for (const account of next.accounts) onRuntimeStatus(account.status);
      } catch {
        // Management connection failures are displayed by the login shell.
      } finally {
        polling = false;
      }
    }, 3000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [section, management, onRuntimeStatus]);

  async function run(work: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    operation.current++;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await work();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  function edit(change: (value: Config) => void) {
    try {
      const next = JSON.parse(draft) as Config;
      if (!next || !Array.isArray(next.accounts))
        throw new Error("请先修正高级配置中的 JSON");
      change(next);
      setDraft(format(next));
      setMessage("");
      setError("");
    } catch (cause) {
      setError(String(cause));
    }
  }
  function editAccount(id: string, patch: Partial<Account>) {
    edit((next) => {
      const index = next.accounts!.findIndex((item) => item.account_id === id);
      next.accounts![index] = { ...next.accounts![index], ...patch };
    });
  }
  function editWebUI(patch: Partial<WebUI>) {
    edit((next) => {
      next.webui = { ...webui, ...patch };
    });
  }
  async function controlAccount(accountId: string, action: AccountAction) {
    await run(async () => {
      const identity = view?.activated_accounts.find(
        (item) => item.account_id === accountId && item.available,
      );
      if (!identity) throw new Error("账号窗口已不可用，请重新连接账号。");
      const connection = new Connection();
      try {
        await connection.connect(accountProfile(identity).url, { accessToken });
        const next = await connection.call(action, {});
        if (next.account_id !== accountId)
          throw new Error("返回的账号与所选账号不一致，请重新连接。");
        setView((current) =>
          current && {
            ...current,
            accounts: current.accounts.map((item) =>
              item.account_id === accountId ? { ...item, status: next } : item,
            ),
          },
        );
        onRuntimeStatus(next);
        if (action === "x_start_listener" && next.listener_state === "failed") {
          throw new Error(next.last_error || "监听启动失败");
        }
        const result = {
          x_connect: "QQ 已绑定",
          x_disconnect: "已解除绑定",
          x_start_listener: next.listening ? "监听已启动" : "已提交启动监听请求",
          x_stop_listener: "监听已停止",
          x_get_runtime_status: "运行状态已更新",
        }[action];
        setMessage(`${identity.nickname}（${accountId}）：${result}。`);
      } finally {
        connection.close();
      }
    });
  }
  async function save() {
    await run(async () => {
      const config = JSON.parse(draft) as Config;
      for (const account of config.accounts || []) {
        const ids = new Set<string>();
        for (const target of account.targets || []) {
          target.id = target.id.trim();
          target.name = target.name.trim();
          if (!/^[1-9][0-9]*$/.test(target.id) || !target.name)
            throw new Error(
              "请为监听会话填写有效的群号或 QQ 号，以及 QQ 窗口中显示的名称。",
            );
          if (ids.has(target.id))
            throw new Error("同一账号的监听范围中有重复号码，请合并后保存。");
          ids.add(target.id);
        }
      }
      const next = (await management.callRaw(
        "x_set_config",
        { config, revision: view!.revision },
        120000,
      )) as ConfigView;
      show(next);
      setMessage(
        Object.keys(next.errors).length
          ? "配置已保存，请处理下面的应用错误。"
          : next.webui.restart_required
            ? "配置已保存；WebUI 地址与端口等待重启，其余设置已应用。"
            : "配置已保存并应用。",
      );
    });
  }

  return (
    <div className="settings-layout">
      <div className="settings-topbar">
        {section === "other" ? (
          <div
            className="settings-tabs"
            role="tablist"
            aria-label="其他设置分类"
          >
            {[
              ["webui", "WebUI 与登录"],
              ["advanced", "高级配置"],
            ].map(([id, label]) => (
              <button
                key={id}
                role="tab"
                aria-selected={tab === id}
                className={tab === id ? "active" : ""}
                onClick={() => setTab(id)}
              >
                {label}
              </button>
            ))}
          </div>
        ) : (
          <div className="settings-state">
            <Badge tone={changed ? "warning" : "success"}>
              {changed ? "有未保存修改" : "独立账号配置"}
            </Badge>
            <span>左侧选择账号，右侧修改配置。</span>
          </div>
        )}
        <div className="button-row">
          <button
            disabled={busy}
            onClick={() =>
              void run(async () =>
                show((await management.callRaw("x_get_config")) as ConfigView),
              )
            }
          >
            <RefreshCw size={15} />
            重新读取
          </button>
          <button
            className="primary"
            disabled={!view || busy || !changed}
            onClick={() => void save()}
          >
            <Save size={15} />
            保存并应用
          </button>
        </div>
      </div>
      {section === "other" && (
        <div className="settings-state">
          <Badge
            tone={
              changed || view?.webui.restart_required ? "warning" : "success"
            }
          >
            {changed
              ? "有未保存修改"
              : view?.webui.restart_required
                ? "WebUI 等待重启"
                : "已读取配置"}
          </Badge>
          <span>登录设置热更新，WebUI 地址和端口重启生效。</span>
        </div>
      )}
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="info-strip" role="status">
          <Check size={17} />
          {message}
        </p>
      )}
      {view &&
        Object.entries(view.errors).map(([key, value]) => (
          <p className="inline-error" role="alert" key={key}>
            {key}：{value}
          </p>
        ))}
      {section === "accounts" &&
        (config && view ? (
          <AccountSettings
            activeAccountId={activeAccountId}
            accessToken={accessToken}
            config={config}
            view={view}
            busy={busy}
            onEdit={editAccount}
            onConnect={onConnect}
            onAction={(accountId, action) =>
              void controlAccount(accountId, action)
            }
          />
        ) : (
          <section className="card">
            <Empty title={view ? "配置需要修正" : "正在读取账号配置"}>
              {view && "打开其他设置中的高级配置，修正 JSON 后继续。"}
            </Empty>
          </section>
        ))}
      {section === "other" && tab === "webui" && (
        <div className="settings-columns">
          <section className="card settings-card">
            <div className="card-heading">
              <span className="tile-icon blue">
                <Globe size={22} />
              </span>
              <div>
                <h2>WebUI 服务</h2>
                <p>控制台的监听地址与端口。</p>
              </div>
            </div>
            <label className="field">
              WebUI 监听地址
              <input
                value={webui.host}
                disabled={!view || busy}
                placeholder="127.0.0.1"
                onChange={(e) => editWebUI({ host: e.target.value })}
              />
            </label>
            <label className="field">
              WebUI 端口
              <input
                type="number"
                min="1024"
                max="65535"
                value={webui.port}
                disabled={!view || busy}
                onChange={(e) => editWebUI({ port: Number(e.target.value) })}
              />
            </label>
            <p className="endpoint-line mono">
              当前地址：{view?.webui.url || "读取中"}
            </p>
            <p className="settings-hint">
              可填写本机 IP、主机名或
              0.0.0.0。保存并重启后端后生效；监听所有网卡时，使用电脑的实际 IP
              访问。
            </p>
            {view?.webui.restart_required && (
              <p className="info-strip">监听配置已保存，等待后端重启。</p>
            )}
            {view?.webui.port_overridden && (
              <p className="info-strip">
                当前端口由 QUITBOT_MANAGEMENT_PORT
                指定，移除该环境设置后才使用这里的端口。
              </p>
            )}
          </section>
          <section className="card settings-card">
            <div className="card-heading">
              <span className="tile-icon purple">
                <KeyRound size={22} />
              </span>
              <div>
                <h2>登录设置</h2>
                <p>保存后应用，现有会话保留原到期时间。</p>
              </div>
            </div>
            <label className="field">
              单次登录有效期（小时）
              <input
                type="number"
                min="0.1"
                max="720"
                step="0.1"
                value={webui.session_hours}
                disabled={!view || busy}
                onChange={(e) =>
                  editWebUI({ session_hours: Number(e.target.value) })
                }
              />
              <small>
                默认 4 小时；后续新登录使用此时长，刷新页面不会续期。
              </small>
            </label>
            <div className="settings-fields">
              <label className="field">
                连续密码错误次数
                <input
                  type="number"
                  min="1"
                  max="20"
                  value={webui.login_attempts}
                  disabled={!view || busy}
                  onChange={(e) =>
                    editWebUI({ login_attempts: Number(e.target.value) })
                  }
                />
              </label>
              <label className="field">
                暂停登录时长（秒）
                <input
                  type="number"
                  min="1"
                  max="3600"
                  value={webui.login_retry_seconds}
                  disabled={!view || busy}
                  onChange={(e) =>
                    editWebUI({ login_retry_seconds: Number(e.target.value) })
                  }
                />
              </label>
            </div>
            <p className="muted">
              首次登录必须设置新密码，修改密码会退出已有会话。
            </p>
            <button onClick={onPasswordChange}>
              <KeyRound size={15} />
              修改登录密码
            </button>
          </section>
        </div>
      )}
      {section === "other" && tab === "advanced" && (
        <section className="card settings-card">
          <div className="card-heading">
            <div>
              <h2>完整运行配置</h2>
              <p>维护会话范围、成员身份映射和其他配置，内容与表单同步。</p>
            </div>
          </div>
          <textarea
            className="config-editor mono"
            aria-label="运行配置 JSON"
            spellCheck={false}
            disabled={!view || busy}
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setMessage("");
            }}
          />
          <small className="muted">
            配置保存在 config/accounts.json。账号设置热更新，WebUI
            地址和端口重启生效。
          </small>
        </section>
      )}
    </div>
  );
}
