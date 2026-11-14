import { useEffect, useRef, useState } from "react";
import { ArrowRight, Monitor, RefreshCw, Search, Unplug } from "lucide-react";
import type { components } from "./generated/management";
import { Connection } from "./transport";
import type { Profile } from "./types";
import { accountProfile } from "./types";
import { Badge, Empty } from "./ui";
import QQLoginPanel from "./QQLoginPanel";

type Discovery = components["schemas"]["DiscoveryView"];
type ConfigView = components["schemas"]["ConfigView"];
type Window = components["schemas"]["DiscoveredProcess"];

export default function ConnectionPage({
  management,
  connectedAccounts,
  busy,
  onAccounts,
  onConnect,
  onDisconnect,
  onSetup,
}: {
  management: Connection;
  connectedAccounts: string[];
  busy: boolean;
  onAccounts: (view: ConfigView) => void;
  onConnect: (profile: Profile) => Promise<void>;
  onDisconnect: (accountId: string) => void;
  onSetup: () => void;
}) {
  const initial = useRef<Promise<unknown> | null>(null);
  const lock = useRef(false);
  const loginSequence = useRef(0);
  const [discovery, setDiscovery] = useState<Discovery>();
  const [accountView, setAccountView] = useState<ConfigView>();
  const [scanning, setScanning] = useState(true);
  const [connecting, setConnecting] = useState<number>();
  const [error, setError] = useState("");
  const [login, setLogin] = useState<{ key: number; window?: Window }>();
  const [loginWorking, setLoginWorking] = useState(false);

  function openLogin(window?: Window) {
    if (scanning || busy || loginWorking || connecting !== undefined) return;
    setLoginWorking(true);
    setLogin({ key: ++loginSequence.current, window });
  }

  useEffect(() => {
    let mounted = true;
    // StrictMode replays effects in development; one page visit issues one scan.
    initial.current ??= management.callRaw("x_refresh_discovery", {}, 120000);
    void initial.current
      .then(async (value) => {
        if (!mounted) return;
        setDiscovery(value as Discovery);
        const view = (await management.callRaw("x_get_config")) as ConfigView;
        if (mounted) {
          setAccountView(view);
          onAccounts(view);
        }
      })
      .catch((cause) => {
        if (mounted) setError(String(cause));
      })
      .finally(() => {
        if (mounted) setScanning(false);
      });
    return () => {
      mounted = false;
    };
  }, [management]);

  async function refresh() {
    if (lock.current || scanning || busy) return;
    lock.current = true;
    setScanning(true);
    setError("");
    try {
      setDiscovery(
        (await management.callRaw(
          "x_refresh_discovery",
          {},
          120000,
        )) as Discovery,
      );
      const view = (await management.callRaw("x_get_config")) as ConfigView;
      setAccountView(view);
      onAccounts(view);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      lock.current = false;
      setScanning(false);
    }
  }

  async function activate(window: Window, expectedAccountId: string) {
    if (lock.current || scanning || busy) return;
    lock.current = true;
    setConnecting(window.hwnd);
    setError("");
    try {
      setDiscovery(
        (await management.callRaw(
          "x_refresh_discovery",
          {},
          120000,
        )) as Discovery,
      );
      const current = (await management.callRaw("x_get_config")) as ConfigView;
      const next = (await management.callRaw(
        "x_activate_account",
        {
          hwnd: window.hwnd,
          revision: current.revision,
          expected_account_id: expectedAccountId,
        },
        120000,
      )) as ConfigView;
      setAccountView(next);
      onAccounts(next);
      const identity = next.activated_accounts.find(
        (item) => item.hwnd === window.hwnd && item.pid === window.pid,
      );
      if (!identity) throw new Error("账号资料尚未完整，请重新连接");
      setLogin(undefined);
      await onConnect(accountProfile(identity));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      lock.current = false;
      setConnecting(undefined);
    }
  }

  async function connectConsole(identity: ConfigView["activated_accounts"][number]) {
    if (lock.current || scanning || busy || loginWorking) return;
    lock.current = true;
    setConnecting(identity.hwnd);
    setError("");
    setLogin(undefined);
    try {
      await onConnect(accountProfile(identity));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      lock.current = false;
      setConnecting(undefined);
    }
  }

  return (
    <>
      <section className="card discovery-summary">
        <span className="tile-icon purple">
          <Search size={22} />
        </span>
        <div>
          <h2>发现本机 QQ</h2>
          <p>
            {scanning
              ? "正在探查 QQ 进程…"
              : `发现 ${discovery?.windows?.length ?? 0} 个 QQ 进程窗口`}
          </p>
          <small>在网页中启动 QQ，选择已保存账号或使用手机扫码登录。</small>
        </div>
        <button
          disabled={scanning || connecting !== undefined || busy}
          onClick={() => void refresh()}
        >
          <RefreshCw size={16} className={scanning ? "spin" : ""} />
          刷新探查
        </button>
        <button
          className="primary"
          disabled={scanning || busy || loginWorking || connecting !== undefined}
          onClick={() => openLogin()}
        >
          启动 QQ / 登录
        </button>
      </section>
      {login && (
        <QQLoginPanel
          key={login.key}
          management={management}
          window={login.window}
          busy={busy || connecting !== undefined}
          onClose={() => setLogin(undefined)}
          onConnect={activate}
          onWorking={setLoginWorking}
        />
      )}
      {(error || discovery?.error) && (
        <p className="inline-error" role="alert">
          {error || discovery?.error}
        </p>
      )}
      <div className="section-toolbar">
        <p>每个账号独立连接，可分别断开控制台。</p>
        <button className="text-button" onClick={onSetup}>
          账号设置 <ArrowRight size={15} />
        </button>
      </div>
      <div className="connection-grid">
        {discovery?.windows?.map((window) => {
          const identity = accountView?.activated_accounts.find(
            (item) => item.hwnd === window.hwnd && item.pid === window.pid,
          );
          const consoleConnected = !!identity?.account_id &&
            connectedAccounts.includes(identity.account_id);
          return (
            <section
              className="card connection-card discovered-account"
              key={window.hwnd}
            >
              <div className="card-heading">
                {identity?.avatar ? (
                  <img
                    className="qq-avatar large"
                    src={identity.avatar}
                    alt="QQ 头像"
                  />
                ) : (
                  <span className="tile-icon purple">
                    <Monitor size={24} />
                  </span>
                )}
                <Badge tone={consoleConnected ? "success" : ""}>
                  {consoleConnected ? "已连接" : identity ? "未连接" : "待确认账号"}
                </Badge>
              </div>
              <h2>{identity?.nickname || "QQ 登录窗口"}</h2>
              <p>
                {identity?.account_id
                  ? `QQ ${identity.account_id}`
                  : "登录或读取此窗口，确认账号身份"}
              </p>
              <div className="card-actions">
                {consoleConnected && identity ? (
                  <button
                    disabled={busy || scanning || connecting !== undefined}
                    onClick={() => onDisconnect(identity.account_id!)}
                  >
                    <Unplug size={15} />
                    断开控制台
                  </button>
                ) : identity ? (
                  <button
                    className="primary"
                    disabled={
                      scanning || busy || loginWorking || connecting !== undefined
                    }
                    onClick={() => void connectConsole(identity)}
                  >
                    {connecting === window.hwnd
                      ? "正在连接…"
                      : "连接控制台"}
                    <ArrowRight size={15} />
                  </button>
                ) : (
                  <button
                    className="primary"
                    disabled={
                      scanning || busy || loginWorking || connecting !== undefined
                    }
                    onClick={() => openLogin(window)}
                  >
                    {connecting === window.hwnd
                      ? "正在获取资料…"
                      : "登录 / 连接账号"}
                    <ArrowRight size={15} />
                  </button>
                )}
              </div>
            </section>
          );
        })}
      </div>
      {!scanning && !discovery?.windows?.length && (
        <section className="card">
          <Empty title="尚未发现 QQ 窗口">
            点击“启动 QQ / 登录”，通过网页完成 QQ 登录。
          </Empty>
        </section>
      )}
      <p className="muted discovery-footnote">
        {discovery?.session_id != null &&
          `Windows 会话 ${discovery.session_id} · `}
        {discovery?.scanned_at &&
          `更新于 ${new Date(discovery.scanned_at * 1000).toLocaleTimeString()} · `}
        首次连接 QQ 时会短暂打开所选账号的本人资料卡。
      </p>
    </>
  );
}
