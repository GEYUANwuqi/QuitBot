import { useEffect, useRef, useState } from "react";
import { ArrowRight, QrCode, RefreshCw, X } from "lucide-react";
import type { components } from "./generated/management";
import { Connection } from "./transport";
import { Select } from "./ui";

type View = components["schemas"]["QQLoginView"];
type Window = components["schemas"]["DiscoveredProcess"];
type Action = components["schemas"]["QQLoginAction"]["action"];

export default function QQLoginPanel({
  management,
  window,
  busy,
  onClose,
  onConnect,
  onWorking,
}: {
  management: Connection;
  window?: Window;
  busy: boolean;
  onClose: () => void;
  onConnect: (window: Window, accountId: string) => Promise<void>;
  onWorking: (working: boolean) => void;
}) {
  const initial = useRef<Promise<unknown> | null>(null);
  const lock = useRef(false);
  const mounted = useRef(true);
  const [view, setView] = useState<View>();
  const [selected, setSelected] = useState("");
  const [working, setWorking] = useState(true);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    onWorking(working);
    return () => onWorking(false);
  }, [working, onWorking]);

  function update(value: unknown) {
    if (!mounted.current) return;
    const next = value as View;
    setView(next);
    setSelected((old) =>
      next.accounts?.some((a) => a.key === old)
        ? old
        : (next.accounts?.find((a) => a.selected)?.key ??
          next.accounts?.[0]?.key ??
          ""),
    );
  }

  useEffect(() => {
    mounted.current = true;
    initial.current ??= management.callRaw(
      "x_start_qq_login",
      window ? { hwnd: window.hwnd, pid: window.pid } : { new_instance: true },
      120000,
    );
    void initial.current
      .then(update)
      .catch((cause) => {
        if (mounted.current) setError(String(cause));
      })
      .finally(() => {
        if (mounted.current) setWorking(false);
      });
    return () => {
      mounted.current = false;
    };
  }, [management, window]);

  async function read() {
    if (!view || lock.current) return;
    lock.current = true;
    setReading(true);
    try {
      update(
        await management.callRaw(
          "x_get_qq_login",
          { login_id: view.login_id },
          30000,
        ),
      );
      if (mounted.current) setError("");
    } catch (cause) {
      if (mounted.current) {
        setError(String(cause));
        // Never leave an old QR displayed after a failed status read.
        setView((old) => (old ? { ...old, qr_image: null } : old));
      }
    } finally {
      lock.current = false;
      if (mounted.current) setReading(false);
    }
  }

  useEffect(() => {
    if (
      !view ||
      error ||
      working ||
      ["logged_in", "closed"].includes(view.state)
    )
      return;
    const timer = setTimeout(() => void read(), 2000);
    return () => clearTimeout(timer);
  }, [view, error, working]);

  async function submit(action: Action) {
    if (!view || lock.current || busy) return;
    lock.current = true;
    setWorking(true);
    setError("");
    setView({ ...view, qr_image: null });
    try {
      update(
        await management.callRaw(
          "x_submit_qq_login",
          {
            login_id: view.login_id,
            action,
            ...(action === "login" ? { account_key: selected } : {}),
          },
          30000,
        ),
      );
    } catch (cause) {
      if (mounted.current) setError(String(cause));
    } finally {
      lock.current = false;
      if (mounted.current) setWorking(false);
    }
  }

  return (
    <section className="card qq-login-panel" aria-label="QQ 登录">
      <div className="card-heading">
        <h2>QQ 登录</h2>
        <button aria-label="关闭登录面板" onClick={onClose}>
          <X size={16} />
        </button>
      </div>
      <p role="status">
        {working ? "正在操作 QQ…" : view?.message || "正在打开 QQ…"}
      </p>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {view?.state === "accounts" && (
        <div className="qq-login-actions">
          <Select
            label="已保存的 QQ 账号"
            value={selected}
            onChange={setSelected}
            disabled={working || busy || reading}
            options={(view.accounts ?? []).map((a) => ({
              value: a.key,
              label: a.account_id
                ? `${a.nickname} · ${a.account_id}`
                : a.nickname,
            }))}
          />
          <button
            className="primary"
            disabled={working || busy || reading || !selected}
            onClick={() => void submit("login")}
          >
            登录所选账号 <ArrowRight size={16} />
          </button>
          <button disabled={working || busy || reading} onClick={() => void submit("qr")}>
            <QrCode size={16} />
            扫码登录其他账号
          </button>
        </div>
      )}
      {view?.state === "qr" && view.qr_image && (
        <div className="qq-login-qr">
          <img src={view.qr_image} alt="QQ 登录二维码，请使用手机 QQ 扫描" />
          <p>请在手机 QQ 中确认登录</p>
        </div>
      )}
      {view?.state === "existing" && (
        <button
          disabled={working || busy || reading}
          onClick={() => void submit("open_existing")}
        >
          打开已登录的 QQ
        </button>
      )}
      {view?.state === "expired" && (
        <button
          disabled={working || busy || reading}
          onClick={() => void submit("refresh_qr")}
        >
          <RefreshCw size={16} />
          刷新二维码
        </button>
      )}
      {view?.can_go_back && (
        <button disabled={working || busy || reading} onClick={() => void submit("back")}>
          返回已保存账号
        </button>
      )}
      {view?.state === "logged_in" && view.profile && (
        <div className="qq-login-actions">
          {view.profile.avatar && (
            <img
              className="qq-login-avatar"
              src={view.profile.avatar}
              alt="QQ 头像"
            />
          )}
          <span>
            {view.profile.nickname} · {view.profile.account_id}
          </span>
          <button
            className="primary"
            disabled={working || busy || reading}
            onClick={() =>
              void onConnect(
                {
                  hwnd: view.profile!.hwnd,
                  pid: view.profile!.pid,
                  process_name: "QQ",
                },
                view.profile!.account_id!,
              )
            }
          >
            连接此账号 <ArrowRight size={16} />
          </button>
        </div>
      )}
      {error && view && (
        <button disabled={working || busy || reading} onClick={() => void read()}>
          <RefreshCw size={16} />
          读取当前状态
        </button>
      )}
      {view?.state === "closed" && (
        <p>关闭此面板后，可点击“启动 QQ / 登录”重新打开。</p>
      )}
    </section>
  );
}
