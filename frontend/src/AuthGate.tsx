import { useEffect, useState } from "react";
import {
  ArrowRight,
  KeyRound,
  LoaderCircle,
  LogOut,
  ShieldCheck,
} from "lucide-react";
import App from "./App";
import { Connection, type SessionCredential } from "./transport";
import { saved } from "./types";

const storageKey = "quitbot.session";
const endpoint = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;

function storedSession(): SessionCredential | undefined {
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey) || "null");
    if (
      value &&
      typeof value.access_token === "string" &&
      typeof value.expires_at === "number" &&
      value.expires_at * 1000 > Date.now()
    )
      return value;
  } catch {
    /* Start at login when storage is unavailable. */
  }
}

export default function AuthGate() {
  const [client] = useState(() => new Connection());
  const [session, setSession] = useState<SessionCredential>();
  const [checking, setChecking] = useState(true);
  const [changing, setChanging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [password, setPassword] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");

  function remember(value?: SessionCredential) {
    setSession(value);
    try {
      sessionStorage.removeItem("quitbot.access-token");
      if (value) sessionStorage.setItem(storageKey, JSON.stringify(value));
      else sessionStorage.removeItem(storageKey);
    } catch {
      /* Keep the session in memory. */
    }
  }

  function endSession(message = "") {
    client.close();
    remember();
    setChanging(false);
    setPassword("");
    setCurrentPassword("");
    setNewPassword("");
    setConfirmation("");
    setError(message);
    setChecking(false);
  }

  useEffect(() => {
    let cancelled = false;
    document.documentElement.dataset.theme = saved("quitbot.theme", "light");
    client.onClose = (reason) => {
      if (!cancelled)
        endSession(
          reason.startsWith("4401")
            ? "登录已过期或已退出，请重新登录"
            : "本机服务连接已断开，请确认后端正在运行后重新登录",
        );
    };
    const stored = storedSession();
    if (stored) {
      void client
        .connect(endpoint, { accessToken: stored.access_token })
        .then(
          (info) => {
            if (!cancelled) remember({ ...stored, ...info });
          },
          () => {
            if (!cancelled) endSession("请重新登录");
          },
        )
        .finally(() => {
          if (!cancelled) setChecking(false);
        });
    } else {
      remember();
      setChecking(false);
    }
    return () => {
      cancelled = true;
      client.close();
    };
  }, [client]);

  useEffect(() => {
    if (!session) return;
    let timer: ReturnType<typeof setTimeout>;
    const expire = () => {
      clearTimeout(timer);
      const remaining = session.expires_at * 1000 - Date.now();
      if (remaining <= 0) endSession("本次登录已到期，请重新登录");
      else timer = setTimeout(expire, Math.min(remaining, 2_147_483_647));
    };
    expire();
    document.addEventListener("visibilitychange", expire);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", expire);
    };
  }, [session]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setError("");
    if (session && newPassword !== confirmation) {
      setError("两次输入的密码不一致");
      return;
    }
    setBusy(true);
    try {
      const value = session
        ? ((await client.callRaw("x_change_password", {
            new_password: newPassword,
            ...(!session.must_change_password && {
              current_password: currentPassword,
            }),
          })) as SessionCredential)
        : ((await client.connect(endpoint, { password })) as SessionCredential);
      remember(value);
      setPassword("");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmation("");
      setChanging(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "登录失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    try {
      await client.callRaw("x_logout", {}, 5000);
    } finally {
      endSession();
    }
  }

  if (session && !session.must_change_password && !changing && !checking)
    return (
      <App
        key={session.access_token}
        session={session}
        management={client}
        onLogout={() => void logout().catch(() => {})}
        onPasswordChange={() => {
          setError("");
          setChanging(true);
        }}
      />
    );

  const change = !!session;
  return (
    <main className="auth-shell">
      <section className="auth-card">
        <div className="auth-brand">
          <span className="brand-icon">Q</span>
          <strong>QuitBot</strong>
        </div>
        <span className="auth-symbol">
          {change ? <KeyRound size={24} /> : <ShieldCheck size={24} />}
        </span>
        <h1>
          {checking ? "正在验证登录" : change ? "修改登录密码" : "登录 QuitBot"}
        </h1>
        <p className="auth-description">
          {change
            ? session.must_change_password
              ? "首次登录，请先修改临时密码，再进入控制台。"
              : "修改后会结束其他登录会话。"
            : "管理你的 QQ 工作空间"}
        </p>
        {checking ? (
          <div className="auth-loading">
            <LoaderCircle className="spin" size={24} />
          </div>
        ) : (
          <form onSubmit={(event) => void submit(event)}>
            {!change ? (
              <label className="field">
                登录密码
                <input
                  type="password"
                  autoComplete="current-password"
                  autoFocus
                  required
                  maxLength={128}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={busy}
                />
                <small>首次使用请输入启动终端显示的临时密码。</small>
              </label>
            ) : (
              <>
                {!session.must_change_password && (
                  <label className="field">
                    当前密码
                    <input
                      type="password"
                      autoComplete="current-password"
                      required
                      maxLength={128}
                      value={currentPassword}
                      onChange={(e) => setCurrentPassword(e.target.value)}
                      disabled={busy}
                    />
                  </label>
                )}
                <label className="field">
                  新密码
                  <input
                    type="password"
                    autoComplete="new-password"
                    autoFocus
                    required
                    minLength={8}
                    maxLength={128}
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    disabled={busy}
                  />
                  <small>8–128 个字符，与当前密码不同。</small>
                </label>
                <label className="field">
                  确认新密码
                  <input
                    type="password"
                    autoComplete="new-password"
                    required
                    minLength={8}
                    maxLength={128}
                    value={confirmation}
                    onChange={(e) => setConfirmation(e.target.value)}
                    disabled={busy}
                  />
                </label>
              </>
            )}
            {error && (
              <p className="inline-error" role="alert">
                {error}
              </p>
            )}
            <button className="primary auth-submit" disabled={busy}>
              {busy ? (
                <LoaderCircle size={17} className="spin" />
              ) : (
                <ArrowRight size={17} />
              )}
              {busy ? "正在处理…" : change ? "修改密码并进入" : "登录"}
            </button>
          </form>
        )}
        {change && (
          <div className="auth-actions">
            {!session.must_change_password && (
              <button disabled={busy} onClick={() => setChanging(false)}>
                返回控制台
              </button>
            )}
            <button
              className="text-button"
              disabled={busy}
              onClick={() => void logout().catch(() => {})}
            >
              <LogOut size={15} />
              退出登录
            </button>
          </div>
        )}
        <p className="auth-footer">
          独立账号配置 · 登录有效期可在其他设置中调整
        </p>
      </section>
    </main>
  );
}
