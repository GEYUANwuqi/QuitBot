import { useEffect, useRef, useState } from "react";
import { Check, RefreshCw, Search } from "lucide-react";
import { Connection, type Result } from "./transport";
import type { Target } from "./types";
import { Badge, Empty, Modal } from "./ui";

export default function ConversationDiscovery({
  accountId,
  accessToken,
  targets,
  onAdd,
  onClose,
}: {
  accountId: string;
  accessToken: string;
  targets: Target[];
  onAdd: (targets: Target[]) => void;
  onClose: () => void;
}) {
  const client = useRef<Connection | undefined>(undefined);
  const active = useRef(false);
  const lock = useRef(true);
  const [busy, setBusy] = useState(true);
  const [progress, setProgress] = useState("正在探查已加载会话…");
  const [error, setError] = useState("");
  const [rows, setRows] = useState<Result<"x_discover_conversations">>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [failures, setFailures] = useState(new Map<string, string>());
  const [query, setQuery] = useState("");
  const [added, setAdded] = useState(0);
  const existing = new Set(targets.map((target) => target.name));
  const visible = rows.filter((row) =>
    row.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );

  useEffect(() => {
    const connection = new Connection();
    client.current = connection;
    active.current = true;
    let current = true;
    void (async () => {
      try {
        const scheme = location.protocol === "https:" ? "wss:" : "ws:";
        await connection.connect(
          `${scheme}//${location.host}/accounts/${accountId}/ws`,
          { accessToken },
        );
        if (!current) return;
        const status = await connection.call("x_get_runtime_status", {});
        if (!current) return;
        if (!status.connected) await connection.call("x_connect", {});
        if (!current) return;
        const found = await connection.call("x_discover_conversations", {});
        if (current) setRows(found);
      } catch (cause) {
        if (current)
          setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        if (current) {
          lock.current = false;
          setBusy(false);
          setProgress("");
        }
      }
    })();
    return () => {
      current = false;
      active.current = false;
      connection.close();
    };
  }, [accountId, accessToken]);

  async function refresh() {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    setProgress("正在刷新会话列表…");
    try {
      const connection = client.current!;
      const scheme = location.protocol === "https:" ? "wss:" : "ws:";
      await connection.connect(
        `${scheme}//${location.host}/accounts/${accountId}/ws`,
        { accessToken },
      );
      if (!active.current) return;
      const status = await connection.call("x_get_runtime_status", {});
      if (!active.current) return;
      if (!status.connected) await connection.call("x_connect", {});
      if (!active.current) return;
      const found = await connection.call("x_discover_conversations", {});
      if (!active.current) return;
      setRows(found);
      setSelected((old) =>
        old.filter((name) =>
          found.some((row) => row.name === name && row.selectable),
        ),
      );
      setFailures(new Map());
    } catch (cause) {
      if (active.current)
        setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (active.current) {
        lock.current = false;
        setBusy(false);
        setProgress("");
      }
    }
  }

  async function add() {
    if (lock.current || !selected.length) return;
    lock.current = true;
    setBusy(true);
    setError("");
    const resolved: Target[] = [];
    const failed = new Map<string, string>();
    for (const [index, name] of selected.entries()) {
      if (!active.current) break;
      setProgress(`正在核验 ${index + 1}/${selected.length}：${name}`);
      try {
        const target = await client.current!.call("x_resolve_conversation", {
          name,
        });
        if (!active.current) break;
        if (
          targets.some(
            (item) => item.id === target.id && item.kind !== target.kind,
          ) ||
          resolved.some(
            (item) => item.id === target.id && item.kind !== target.kind,
          )
        )
          throw new Error("此号码已用于其他类型的会话，请检查现有监听范围");
        resolved.push(target);
      } catch (cause) {
        failed.set(name, cause instanceof Error ? cause.message : String(cause));
      }
    }
    if (!active.current) return;
    if (resolved.length) {
      onAdd(resolved);
      setAdded((old) => old + resolved.length);
    }
    setFailures(failed);
    setSelected([...failed.keys()]);
    lock.current = false;
    setBusy(false);
    setProgress("");
    if (!failed.size) onClose();
  }

  return (
    <Modal title="探查并选择监听会话" onClose={onClose} busy={busy}>
      <p className="modal-context">
        QQ {accountId} · 当前已加载的最近会话。未找到的会话可先在 QQ
        中打开，再刷新。
      </p>
      <p className="modal-context">
        勾选后读取号码与类型，再加入配置。核验使用 QQ
        的复制号码菜单，会更新剪贴板。
      </p>
      <div className="conversation-discovery-tools">
        <label className="search-field">
          <Search size={16} />
          <input
            aria-label="筛选会话"
            placeholder="按会话名称筛选"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <button disabled={busy} onClick={() => void refresh()}>
          <RefreshCw size={15} />
          刷新
        </button>
      </div>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {added > 0 && (
        <p className="info-strip" role="status">
          <Check size={16} />
          已添加 {added} 个会话，保存配置后生效。
        </p>
      )}
      {busy && (
        <p className="muted" role="status">
          {progress}
        </p>
      )}
      <div
        className="conversation-discovery-list"
        aria-label="探查到的会话"
        aria-busy={busy}
      >
        {visible.map((row) => {
          const included = existing.has(row.name);
          return (
            <label
              className={`conversation-discovery-row${selected.includes(row.name) ? " selected" : ""}`}
              key={row.name}
            >
              <input
                type="checkbox"
                aria-label={`监听 ${row.name}`}
                checked={included || selected.includes(row.name)}
                disabled={busy || included || !row.selectable}
                onChange={(event) =>
                  setSelected((old) =>
                    event.target.checked
                      ? [...old, row.name]
                      : old.filter((name) => name !== row.name),
                  )
                }
              />
              <span>
                <strong>{row.name}</strong>
                {row.reason && <small>{row.reason}</small>}
                {failures.has(row.name) && (
                  <small className="inline-error">{failures.get(row.name)}</small>
                )}
              </span>
              {included && <Badge>已添加</Badge>}
            </label>
          );
        })}
        {!busy && !visible.length && (
          <Empty title={rows.length ? "没有匹配的会话" : "未找到已加载会话"}>
            {rows.length
              ? "换个名称再试。"
              : "在 QQ 消息页打开需要监听的群聊或私聊，再刷新。"}
          </Empty>
        )}
      </div>
      <div className="modal-actions">
        <span className="muted">已选 {selected.length} 个</span>
        <button disabled={busy} onClick={onClose}>
          取消
        </button>
        <button
          className="primary"
          disabled={busy || !selected.length}
          onClick={() => void add()}
        >
          {busy ? "处理中…" : "核验并添加"}
        </button>
      </div>
    </Modal>
  );
}
