import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity as ActivityIcon,
  ArrowRight,
  Cable,
  CheckCircle2,
  ChevronRight,
  Code2,
  ExternalLink,
  LayoutDashboard,
  KeyRound,
  LoaderCircle,
  LogOut,
  Menu,
  Moon,
  Radio,
  Settings,
  Sun,
  Users,
  X,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { Connection, RpcError, type SessionCredential } from "./transport";
import type { components as managementTypes } from "./generated/management";
import ConnectionPage from "./ConnectionPage";
import SettingsPage from "./SettingsPage";
import Overview from "./Overview";
import { Events, Explorer } from "./DebugPages";
import type {
  Activity,
  Catalog,
  Execute,
  Page,
  Profile,
  ReceivedEvent,
  Status,
} from "./types";
import {
  accountProfile,
  actionLabel,
  listenerLabel,
  persist,
  saved,
} from "./types";
import { Badge, Select } from "./ui";

const navigation: {
  group: string;
  items: { id: Page; label: string; icon: LucideIcon }[];
}[] = [
  {
    group: "工作台",
    items: [
      { id: "overview", label: "概览", icon: LayoutDashboard },
      { id: "connections", label: "账号连接", icon: Cable },
      { id: "accounts", label: "账号设置", icon: Users },
      { id: "settings", label: "其他设置", icon: Settings },
    ],
  },
  {
    group: "开发工具",
    items: [
      { id: "api", label: "接口调试", icon: Code2 },
      { id: "events", label: "实时事件", icon: ActivityIcon },
    ],
  },
];
const allPages = navigation.flatMap((section) => section.items);
const descriptions: Record<Page, string> = {
  overview: "查看账号状态、监听范围与最近调用。",
  connections: "探查 QQ 进程，连接时获取账号资料。",
  accounts: "按账号管理窗口、监听与网络连接，保存后自动应用。",
  settings: "设置 WebUI、登录有效期与完整运行配置。",
  api: "选择接口、填写参数，通过 WebSocket 查看真实回执。",
  events: "观察消息、通知和请求的实时推送。",
};
function route(): Page {
  const id = location.hash.slice(1);
  return allPages.some((page) => page.id === id) ? (id as Page) : "overview";
}
export default function App({
  session,
  management,
  onLogout,
  onPasswordChange,
}: {
  session: SessionCredential;
  management: Connection;
  onLogout: () => void;
  onPasswordChange: () => void;
}) {
  const client = useRef(new Connection());
  const clients = useRef(new Map<string, Connection>());
  const opening = useRef(new Map<string, Promise<Connection>>());
  const mounted = useRef(true);
  const [consoleAccounts, setConsoleAccounts] = useState<string[]>([]);
  const [closedAccounts] = useState(() => {
    try {
      const value: unknown = JSON.parse(
        sessionStorage.getItem("quitbot.closed-accounts") || "[]",
      );
      return new Set<string>(
        Array.isArray(value)
          ? value.filter(
              (id): id is string =>
                typeof id === "string" && /^[1-9][0-9]*$/.test(id),
            )
          : [],
      );
    } catch {
      return new Set<string>();
    }
  });
  const lock = useRef(false);
  const generation = useRef(0);
  const selectedProfile = useRef<Profile | undefined>(undefined);
  const eventId = useRef(0);
  const callId = useRef(0);
  const [page, setPage] = useState<Page>(route);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const accessToken = session.access_token;
  const [connected, setConnected] = useState(false);
  const [status, setStatus] = useState<Status>();
  const [catalog, setCatalog] = useState<Catalog>();
  const [activeUrl, setActiveUrl] = useState("");
  const [accountScope, setAccountScope] = useState(0);
  const [targetId, setTargetId] = useState("");
  const [events, setEvents] = useState<ReceivedEvent[]>([]);
  const [activities, setActivities] = useState<Activity[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<{ title: string; message: string }>();
  const [toast, setToast] = useState<{
    title: string;
    detail?: string;
    ok: boolean;
    id: number;
  }>();
  const [sidebar, setSidebar] = useState(false);
  const [theme, setTheme] = useState(() =>
    saved(
      "quitbot.theme",
      matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light",
    ),
  );
  const target = status?.targets.find(
    (item) => `${item.kind}:${item.id}` === targetId,
  );
  const current = allPages.find((item) => item.id === page)!;
  const showBusinessStatus = ["overview", "api", "events"].includes(page);

  function syncAccounts(view: managementTypes["schemas"]["ConfigView"]) {
    setProfiles(
      view.activated_accounts.map((identity) => {
        const profile = accountProfile(identity);
        const http = view.config.accounts?.find(
          (account) => account.account_id === profile.id,
        )?.http;
        if (http?.enabled) {
          const host = ["0.0.0.0", "::"].includes(http.host || "")
            ? location.hostname
            : http.host || "127.0.0.1";
          profile.docsUrl = `http://${host.includes(":") && !host.startsWith("[") ? `[${host}]` : host}:${http.port}/docs`;
        }
        return profile;
      }),
    );
  }
  useEffect(() => {
    let active = true;
    void management
      .callRaw("x_get_config")
      .then((value) => {
        if (active)
          syncAccounts(value as managementTypes["schemas"]["ConfigView"]);
      })
      .catch((cause) => {
        if (active) setError({ title: "配置读取失败", message: String(cause) });
      });
    return () => {
      active = false;
    };
  }, [management]);

  function navigate(next: Page) {
    location.hash = next;
    setPage(next);
    setSidebar(false);
  }
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
  }, [page]);
  function applyStatus(next: Status) {
    setStatus(next);
    setTargetId((old) =>
      next.targets.some((t) => `${t.kind}:${t.id}` === old)
        ? old
        : next.targets[0]
          ? `${next.targets[0].kind}:${next.targets[0].id}`
          : "",
    );
  }
  function resetAccount() {
    generation.current++;
    selectedProfile.current = undefined;
    setConnected(false);
    setStatus(undefined);
    setCatalog(undefined);
    setTargetId("");
    setActiveUrl("");
    setEvents([]);
    setActivities([]);
    setAccountScope((old) => old + 1);
  }
  const updateAccountStatus = useCallback((next: Status) => {
    if (selectedProfile.current?.id === next.account_id) applyStatus(next);
  }, []);
  function configureConnection(connection: Connection, profile: Profile) {
    connection.onEvent = (packet) => {
      if (selectedProfile.current?.id !== profile.id) return;
      setEvents((old) =>
        [{ id: ++eventId.current, at: Date.now(), packet }, ...old].slice(
          0,
          200,
        ),
      );
      if (
        packet.post_type === "quitbot" &&
        packet.detail_type === "listener_error"
      ) {
        const message = String(
          packet.message || "监听初始化失败，请检查 QQ 窗口",
        );
        setStatus((old) =>
          old
            ? {
                ...old,
                listening: false,
                listener_state: "failed",
                last_error: message,
              }
            : old,
        );
        setError({ title: "消息监听失败", message });
        setToast(undefined);
      }
    };
    connection.onClose = (reason) => {
      if (clients.current.get(profile.id) !== connection) return;
      clients.current.delete(profile.id);
      setConsoleAccounts((old) => old.filter((id) => id !== profile.id));
      if (selectedProfile.current?.id !== profile.id) {
        if (reason.startsWith("1012")) void restoreConsole(profile);
        return;
      }
      const epoch = ++generation.current;
      setConnected(false);
      setStatus(undefined);
      setCatalog(undefined);
      if (reason.startsWith("1012") && selectedProfile.current) {
        setError(undefined);
        setBusy("正在恢复账号连接");
        void restoreConnection(epoch);
        return;
      }
      setBusy("");
      setError({
        title: "服务连接已断开",
        message: `${reason}。请先核对在途操作结果，再重新连接。`,
      });
    };
  }

  function openConsole(profile: Profile): Promise<Connection> {
    const pending = opening.current.get(profile.id);
    if (pending) return pending;
    const existing = clients.current.get(profile.id);
    if (existing) return Promise.resolve(existing);
    const connection = new Connection();
    clients.current.set(profile.id, connection);
    configureConnection(connection, profile);
    const task = connection
      .connect(profile.url, { accessToken })
      .then(() => {
        if (!mounted.current || clients.current.get(profile.id) !== connection)
          throw new Error("账号连接已取消");
        setConsoleAccounts((old) =>
          old.includes(profile.id) ? old : [...old, profile.id],
        );
        return connection;
      })
      .catch((cause) => {
        if (clients.current.get(profile.id) === connection) {
          clients.current.delete(profile.id);
          connection.close();
        }
        throw cause;
      })
      .finally(() => {
        if (opening.current.get(profile.id) === task)
          opening.current.delete(profile.id);
      });
    opening.current.set(profile.id, task);
    return task;
  }

  async function restoreConsole(profile: Profile) {
    const deadline = Date.now() + 45000;
    try {
      while (
        mounted.current &&
        !closedAccounts.has(profile.id) &&
        Date.now() < deadline
      ) {
        if (clients.current.has(profile.id)) return;
        const view = (await management.callRaw("x_get_config")) as
          managementTypes["schemas"]["ConfigView"];
        if (!view.config.accounts?.some((item) => item.account_id === profile.id))
          return;
        if (closedAccounts.has(profile.id) || !mounted.current) return;
        if (view.accounts.some((item) => item.account_id === profile.id)) {
          await openConsole(profile);
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    } catch {
      // The account card remains disconnected; no business action is replayed.
    }
  }

  useEffect(() => {
    for (const profile of profiles) {
      if (!closedAccounts.has(profile.id)) void openConsole(profile).catch(() => {});
    }
  }, [profiles]);

  useEffect(() => {
    mounted.current = true;
    const handleRoute = () => {
      setPage(route());
      setSidebar(false);
    };
    window.addEventListener("hashchange", handleRoute);
    return () => {
      mounted.current = false;
      generation.current++;
      for (const connection of clients.current.values()) connection.close();
      clients.current.clear();
      opening.current.clear();
      window.removeEventListener("hashchange", handleRoute);
    };
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    persist("quitbot.theme", theme);
  }, [theme]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(undefined), 5000);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    if (!connected) return;
    let polling = false;
    let cancelled = false;
    const epoch = generation.current;
    const timer = setInterval(async () => {
      if (polling || lock.current || document.hidden) return;
      polling = true;
      try {
        const next = await client.current.call("x_get_runtime_status", {});
        if (!cancelled && epoch === generation.current) applyStatus(next);
      } catch {
        /* Socket close reports disconnects; never resend a business action. */
      } finally {
        polling = false;
      }
    }, 10000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [connected]);

  const execute: Execute = async (action, params = {}) => {
    if (lock.current)
      return { ok: false, error: "上一个操作尚未结束，请等待回执。" };
    lock.current = true;
    const definition = catalog?.actions.find((item) => item.name === action);
    const label = definition ? actionLabel(definition) : action;
    setBusy(label);
    setError(undefined);
    setToast(undefined);
    const at = Date.now();
    try {
      const data = await client.current.callRaw(action, params);
      if (
        [
          "x_get_runtime_status",
          "x_connect",
          "x_disconnect",
          "x_start_listener",
          "x_stop_listener",
        ].includes(action)
      )
        applyStatus(data as Status);
      if (
        action === "x_start_listener" &&
        (data as Status).listener_state === "failed"
      )
        throw new RpcError(
          (data as Status).last_error || "监听初始化失败",
          100,
        );
      setActivities((old) =>
        [
          {
            id: ++callId.current,
            action,
            params,
            at,
            duration: Date.now() - at,
            ok: true,
            data,
          },
          ...old,
        ].slice(0, 50),
      );
      setToast({
        title:
          action === "x_start_listener" &&
          (data as Status).listener_state === "starting"
            ? "正在准备监听"
            : "操作成功",
        detail: label,
        ok: true,
        id: Date.now(),
      });
      return { ok: true, data };
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      const title =
        cause instanceof RpcError && cause.retcode === 101
          ? "操作结果待核验"
          : "操作未完成";
      setError({ title, message });
      setActivities((old) =>
        [
          {
            id: ++callId.current,
            action,
            params,
            at,
            duration: Date.now() - at,
            ok: false,
            error: message,
            data: cause instanceof RpcError ? cause.data : undefined,
          },
          ...old,
        ].slice(0, 50),
      );
      setToast({ title, detail: label, ok: false, id: Date.now() });
      return { ok: false, error: message };
    } finally {
      lock.current = false;
      setBusy("");
    }
  };

  async function restoreConnection(epoch: number) {
    const profile = selectedProfile.current;
    if (!profile) return;
    const deadline = Date.now() + 45000;
    try {
      while (epoch === generation.current && Date.now() < deadline) {
        const view = (await management.callRaw(
          "x_get_config",
          {},
          // The management socket processes config updates serially. This read
          // may wait behind UI initialization; allow the whole recovery window.
          Math.max(1, deadline - Date.now()),
        )) as managementTypes["schemas"]["ConfigView"];
        if (epoch !== generation.current) return;
        if (
          !view.config.accounts?.some((item) => item.account_id === profile.id)
        )
          throw new Error("当前账号已从配置中移除。");
        if (
          view.accounts.some((item) => item.account_id === profile.id) &&
          !lock.current
        ) {
          syncAccounts(view);
          await connect(profile, true);
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
      if (epoch === generation.current)
        throw new Error("账号服务恢复超时，请查看账号设置中的应用错误。");
    } catch (cause) {
      if (epoch === generation.current) {
        setBusy("");
        setError({
          title: "账号连接恢复失败",
          message: cause instanceof Error ? cause.message : String(cause),
        });
      }
    }
  }

  async function connect(profile: Profile, restoring = false, openSettings = true) {
    if (lock.current) return;
    lock.current = true;
    setBusy("连接账号服务");
    setError(undefined);
    setToast(undefined);
    if (!restoring) resetAccount();
    else generation.current++;
    selectedProfile.current = profile;
    const epoch = generation.current;
    let connection: Connection | undefined;
    try {
      if (!accessToken) throw new Error("请重新登录");
      closedAccounts.delete(profile.id);
      rememberClosedAccounts();
      connection = await openConsole(profile);
      if (epoch !== generation.current) return;
      client.current = connection;
      // Metadata reads never bind a native QQ window.
      const inventory = await client.current.call("x_get_api_catalog", {});
      const next = await client.current.call("x_get_runtime_status", {});
      if (epoch !== generation.current) return;
      setCatalog(inventory as unknown as Catalog);
      applyStatus(next);
      setConnected(true);
      setActiveUrl(profile.url);
      setToast({
        title: restoring ? "账号连接已恢复" : "账号服务已连接",
        detail: `${next.label} · ${next.account_id}`,
        ok: true,
        id: Date.now(),
      });
      if (!restoring && openSettings) navigate("accounts");
    } catch (cause) {
      if (epoch !== generation.current) return;
      if (connection && clients.current.get(profile.id) === connection) {
        clients.current.delete(profile.id);
        connection.close();
        setConsoleAccounts((old) => old.filter((id) => id !== profile.id));
      }
      resetAccount();
      setBusy("");
      setError({
        title: "连接失败",
        message: cause instanceof Error ? cause.message : String(cause),
      });
    } finally {
      lock.current = false;
      if (epoch === generation.current) setBusy("");
    }
  }
  function rememberClosedAccounts() {
    try {
      sessionStorage.setItem(
        "quitbot.closed-accounts",
        JSON.stringify([...closedAccounts]),
      );
    } catch {
      // Keep explicit disconnects in memory when tab storage is unavailable.
    }
  }
  function disconnect(accountId: string) {
    if (lock.current) return;
    closedAccounts.add(accountId);
    rememberClosedAccounts();
    const connection = clients.current.get(accountId);
    clients.current.delete(accountId);
    connection?.close();
    setConsoleAccounts((old) => old.filter((id) => id !== accountId));
    if (selectedProfile.current?.id === accountId) resetAccount();
    setError(undefined);
    setToast(undefined);
  }
  const docsUrl =
    profiles.find((profile) => profile.id === status?.account_id)?.docsUrl ||
    "";
  return (
    <div className="app-shell">
      {sidebar && (
        <button
          className="sidebar-scrim"
          aria-label="关闭导航"
          onClick={() => setSidebar(false)}
        />
      )}
      <aside className={`sidebar ${sidebar ? "is-open" : ""}`}>
        <a
          className="brand"
          href="#overview"
          onClick={() => navigate("overview")}
        >
          <span className="brand-mark">
            Q<span />
          </span>
          <span>
            QuitBot<small>QQ AUTOMATION</small>
          </span>
          <Badge>WEBUI</Badge>
        </a>
        <nav aria-label="主导航">
          {navigation.map((section) => (
            <div className="nav-group" key={section.group}>
              <p>{section.group}</p>
              {section.items.map((item) => (
                <button
                  title={item.label}
                  className={`nav-item ${page === item.id ? "active" : ""}`}
                  key={item.id}
                  aria-current={page === item.id ? "page" : undefined}
                  onClick={() => navigate(item.id)}
                >
                  <item.icon size={19} />
                  <span>{item.label}</span>
                  {item.id === "events" && events.length > 0 && (
                    <em>{events.length}</em>
                  )}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div>
            <span className={`status-dot ${consoleAccounts.length ? "online" : ""}`} />
            <strong>
              {consoleAccounts.length
                ? `已连接 ${consoleAccounts.length} 个账号`
                : "账号服务未连接"}
            </strong>
          </div>
          <small>每个账号，独立连接</small>
          <span className="sidebar-version">
            QuitBot <span>v{catalog?.version || "0.12.0"}</span>
          </span>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <button
            className="icon-button mobile-menu"
            aria-label="打开导航"
            onClick={() => setSidebar(true)}
          >
            <Menu size={20} />
          </button>
          <div className="breadcrumb">
            工作空间
            <ChevronRight size={13} />
            <strong>{current.label}</strong>
          </div>
          <div className="topbar-actions">
            <span className="session-expiry">
              登录有效至{" "}
              {new Date(session.expires_at * 1000).toLocaleTimeString("zh-CN", {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </span>
            <span className={`connection-label ${consoleAccounts.length ? "online" : ""}`}>
              <span className={`status-dot ${consoleAccounts.length ? "online" : ""}`} />
              {consoleAccounts.length ? `已连接 ${consoleAccounts.length} 个账号` : "账号未连接"}
            </span>
            <span className="topbar-divider" />
            <button
              className="icon-button"
              aria-label={theme === "dark" ? "切换浅色主题" : "切换深色主题"}
              title={theme === "dark" ? "浅色模式" : "深色模式"}
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            >
              {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
            </button>
            <button
              className="header-avatar"
              title="管理账号连接"
              aria-label="管理账号连接"
              onClick={() => navigate("connections")}
            >
              {status?.label?.[0] || "Q"}
            </button>
            <button
              className="icon-button"
              aria-label="修改登录密码"
              title="修改登录密码"
              onClick={onPasswordChange}
            >
              <KeyRound size={18} />
            </button>
            <button
              className="icon-button"
              aria-label="退出登录"
              title="退出登录"
              onClick={onLogout}
            >
              <LogOut size={18} />
            </button>
          </div>
        </header>
        <div className="content">
          <div className="page-heading">
            <div>
              <h1>{current.label}</h1>
              <p>{descriptions[page]}</p>
            </div>
            <div className="heading-actions">
              {showBusinessStatus && (
                <Select
                  label="账号"
                  value={status?.account_id || ""}
                  options={profiles
                    .filter((profile) => consoleAccounts.includes(profile.id))
                    .map((profile) => ({
                      value: profile.id,
                      label: `${profile.name} · ${profile.id}`,
                    }))}
                  disabled={!!busy}
                  onChange={(id) => {
                    const profile = profiles.find((item) => item.id === id);
                    if (profile) void connect(profile, false, false);
                  }}
                />
              )}
              {showBusinessStatus && (
                <Badge tone={status?.connected ? "success" : ""}>
                  <span
                    className={`status-dot ${status?.connected ? "online" : ""}`}
                  />
                  {status?.connected ? "QQ 已绑定" : "QQ 未绑定"}
                </Badge>
              )}
            </div>
          </div>
          {error && (
            <div className="alert" role="alert">
              <XCircle size={19} />
              <div>
                <strong>{error.title}</strong>
                <p>{error.message}</p>
              </div>
              <button
                className="icon-button"
                aria-label="关闭错误提示"
                onClick={() => setError(undefined)}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {!connected &&
            !["overview", "connections", "accounts", "settings"].includes(
              page,
            ) && (
              <div className="connection-reminder">
                <Cable size={18} />
                <span>连接账号服务后即可使用此页面。</span>
                <button
                  className="text-button"
                  onClick={() => navigate("connections")}
                >
                  前往连接
                  <ArrowRight size={14} />
                </button>
              </div>
            )}
          {page === "overview" && (
            <Overview
              connected={connected}
              status={status}
              catalog={catalog}
              endpoint={activeUrl}
              activities={activities}
              eventCount={events.length}
            />
          )}
          {page === "connections" && (
            <ConnectionPage
              management={management}
              connectedAccounts={consoleAccounts}
              busy={!!busy}
              onSetup={() => navigate("accounts")}
              onAccounts={syncAccounts}
              onConnect={connect}
              onDisconnect={disconnect}
            />
          )}
          {(page === "settings" || page === "accounts") && (
            <SettingsPage
              section={page === "accounts" ? "accounts" : "other"}
              activeAccountId={
                status?.account_id || selectedProfile.current?.id
              }
              management={management}
              accessToken={accessToken}
              onAccounts={syncAccounts}
              onPasswordChange={onPasswordChange}
              onConnect={() => navigate("connections")}
              onRuntimeStatus={updateAccountStatus}
            />
          )}
          {page === "api" && (
            <Explorer
              key={accountScope}
              catalog={catalog}
              target={target}
              execute={execute}
              disabled={!connected || !!busy}
              busy={!!busy}
              activities={activities}
            />
          )}
          {page === "events" && (
            <Events
              key={accountScope}
              events={events}
              onClear={() => setEvents([])}
              listening={!!status?.listening}
              connected={connected}
            />
          )}
          <footer>
            <span>
              QuitBot <span className="footer-dot">·</span> Desktop UI
              Automation
            </span>
            <span>
              {connected && showBusinessStatus && (
                <>
                  <Radio size={12} />
                  {listenerLabel(status)}
                </>
              )}
              {docsUrl && connected && showBusinessStatus && (
                <a href={docsUrl} target="_blank" rel="noreferrer">
                  OpenAPI 文档
                  <ExternalLink size={12} />
                </a>
              )}
            </span>
          </footer>
        </div>
      </main>
      {busy ? (
        <div className="operation-toast running" role="status">
          <LoaderCircle size={21} className="spin" />
          <div>
            <strong>正在执行</strong>
            <small>{busy}</small>
          </div>
        </div>
      ) : (
        toast && (
          <div
            className={`operation-toast ${toast.ok ? "success" : "failed"}`}
            role="status"
            key={toast.id}
          >
            {toast.ok ? <CheckCircle2 size={21} /> : <XCircle size={21} />}
            <div>
              <strong>{toast.title}</strong>
              <small>{toast.detail}</small>
            </div>
            <button
              className="icon-button"
              aria-label="关闭操作提示"
              onClick={() => setToast(undefined)}
            >
              <X size={15} />
            </button>
            <span className="toast-timer" />
          </div>
        )
      )}
    </div>
  );
}
