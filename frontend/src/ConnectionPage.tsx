import { useEffect, useRef, useState } from "react";
import { ArrowRight, Monitor, RefreshCw, Search, Unplug } from "lucide-react";
import type { components } from "./generated/management";
import { Connection } from "./transport";
import type { Profile, Status } from "./types";
import { accountProfile } from "./types";
import { Badge, Empty } from "./ui";

type Discovery = components["schemas"]["DiscoveryView"];
type ConfigView = components["schemas"]["ConfigView"];
type Window = components["schemas"]["DiscoveredProcess"];

export default function ConnectionPage({
  management,
  activeId,
  connected,
  status,
  busy,
  onAccounts,
  onConnect,
  onDisconnect,
  onSetup,
}: {
  management: Connection;
  activeId?: string;
  connected: boolean;
  status?: Status;
  busy: boolean;
  onAccounts: (view: ConfigView) => void;
  onConnect: (profile: Profile) => Promise<void>;
  onDisconnect: () => void;
  onSetup: () => void;
}) {
  const initial = useRef<Promise<unknown> | null>(null);
  const lock = useRef(false);
  const [discovery, setDiscovery] = useState<Discovery>();
  const [activated, setActivated] = useState<ConfigView["activated_accounts"]>(
    [],
  );
  const [scanning, setScanning] = useState(true);
  const [connecting, setConnecting] = useState<number>();
  const [error, setError] = useState("");

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
          setActivated(view.activated_accounts);
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
      setActivated(view.activated_accounts);
      onAccounts(view);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      lock.current = false;
      setScanning(false);
    }
  }

  async function activate(window: Window) {
    if (lock.current || scanning || busy) return;
    lock.current = true;
    setConnecting(window.hwnd);
    setError("");
    try {
      const current = (await management.callRaw("x_get_config")) as ConfigView;
      const next = (await management.callRaw(
        "x_activate_account",
        { hwnd: window.hwnd, revision: current.revision },
        120000,
      )) as ConfigView;
      setActivated(next.activated_accounts);
      onAccounts(next);
      const identity = next.activated_accounts.find(
        (item) => item.hwnd === window.hwnd && item.pid === window.pid,
      );
      if (!identity) throw new Error("账号资料尚未完整，请重新连接");
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
          <small>进入此页自动探查一次。打开或切换 QQ 后，可手动刷新。</small>
        </div>
        <button
          disabled={scanning || connecting !== undefined || busy}
          onClick={() => void refresh()}
        >
          <RefreshCw size={16} className={scanning ? "spin" : ""} />
          刷新探查
        </button>
      </section>
      {(error || discovery?.error) && (
        <p className="inline-error" role="alert">
          {error || discovery?.error}
        </p>
      )}
      <div className="section-toolbar">
        <p>选择进程并连接后，获取 QQ 号、昵称和头像。</p>
        <button className="text-button" onClick={onSetup}>
          账号设置 <ArrowRight size={15} />
        </button>
      </div>
      <div className="connection-grid">
        {discovery?.windows?.map((window) => {
          const current =
            connected &&
            status?.connected &&
            activated.some(
              (item) =>
                item.account_id === activeId &&
                item.hwnd === window.hwnd &&
                item.pid === window.pid,
            );
          return (
            <section
              className={`card connection-card discovered-account ${current ? "current" : ""}`}
              key={window.hwnd}
            >
              <div className="card-heading">
                <span className="tile-icon purple">
                  <Monitor size={24} />
                </span>
                <Badge tone={current ? "success" : ""}>
                  {current ? "当前连接" : "待连接"}
                </Badge>
              </div>
              <h2>{window.process_name}</h2>
              <p className="mono">
                PID {window.pid} · HWND {window.hwnd}
              </p>
              <div className="card-actions">
                {current ? (
                  <button
                    disabled={busy || scanning || connecting !== undefined}
                    onClick={onDisconnect}
                  >
                    <Unplug size={15} />
                    断开控制台
                  </button>
                ) : (
                  <button
                    className="primary"
                    disabled={scanning || busy || connecting !== undefined}
                    onClick={() => void activate(window)}
                  >
                    {connecting === window.hwnd ? "正在获取资料…" : "连接账号"}
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
            在后端所在的 Windows 会话中打开 QQ 主窗口，然后刷新探查。
          </Empty>
        </section>
      )}
      <p className="muted discovery-footnote">
        {discovery?.session_id != null &&
          `Windows 会话 ${discovery.session_id} · `}
        {discovery?.scanned_at &&
          `更新于 ${new Date(discovery.scanned_at * 1000).toLocaleTimeString()} · `}
        点击连接时会短暂打开所选账号的本人资料卡。
      </p>
    </>
  );
}
