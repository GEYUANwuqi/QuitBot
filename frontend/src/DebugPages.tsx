import { useEffect, useMemo, useState } from "react";
import {
  Braces,
  Check,
  ChevronDown,
  Code2,
  History,
  ListFilter,
  Pause,
  Play,
  Search,
  Trash2,
} from "lucide-react";
import type {
  Activity,
  Catalog,
  CatalogAction,
  Execute,
  ReceivedEvent,
  Schema,
  Target,
} from "./types";
import { actionGroup, actionLabel, format } from "./types";
import { Badge, Empty, JsonView, Modal, Select } from "./ui";

function resolve(schema: Schema | undefined, catalog?: Catalog): Schema {
  if (!schema) return {};
  if (schema.$ref) return catalog?.schemas[schema.$ref.split("/").pop()!] || {};
  return schema.anyOf?.find((item) => item.type !== "null")
    ? resolve(
        schema.anyOf.find((item) => item.type !== "null"),
        catalog,
      )
    : schema;
}
function initialValue(key: string, schema: Schema, target?: Target): unknown {
  if (schema.default !== undefined) return schema.default;
  if (schema.enum) return schema.enum[0];
  if (
    key === "target_id" ||
    (key === "group_id" && target?.kind === "group") ||
    (key === "user_id" && target?.kind === "private")
  )
    return target?.id || "";
  return schema.type === "boolean"
    ? false
    : ["integer", "number"].includes(schema.type || "")
      ? 0
      : schema.type === "array"
        ? []
        : schema.type === "object" || schema.properties
          ? {}
          : "";
}

export function Explorer({
  catalog,
  target,
  execute,
  disabled,
  busy,
  activities,
}: {
  catalog?: Catalog;
  target?: Target;
  execute: Execute;
  disabled: boolean;
  busy: boolean;
  activities: Activity[];
}) {
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState("全部分类");
  const [selected, setSelected] = useState("x_get_runtime_status");
  const [input, setInput] = useState("{}");
  const [mode, setMode] = useState<"form" | "json">("form");
  const [outputTab, setOutputTab] = useState("response");
  const [error, setError] = useState("");
  const [invalidFields, setInvalidFields] = useState<string[]>([]);
  const [confirm, setConfirm] = useState<{
    action: string;
    params: Record<string, unknown>;
  }>();
  const action = catalog?.actions.find((item) => item.name === selected);
  const schema = resolve(action?.params, catalog);
  const groups = [
    ...new Set(catalog?.actions.map((item) => actionGroup(item.name))),
  ];
  const filtered =
    catalog?.actions.filter(
      (item) =>
        (group === "全部分类" || actionGroup(item.name) === group) &&
        `${item.name} ${actionLabel(item)} ${actionGroup(item.name)}`
          .toLowerCase()
          .includes(query.toLowerCase()),
    ) || [];
  const recent = activities.find((item) => item.action === selected);
  let body: Record<string, unknown> = {};
  let valid = true;
  try {
    body = JSON.parse(input);
    if (!body || Array.isArray(body) || typeof body !== "object") valid = false;
  } catch {
    valid = false;
  }
  function choose(item: CatalogAction) {
    setSelected(item.name);
    setError("");
    setInvalidFields([]);
    const params = resolve(item.params, catalog);
    setInput(
      format(
        Object.fromEntries(
          (params.required || []).map((key) => [
            key,
            initialValue(
              key,
              resolve(params.properties?.[key], catalog),
              target,
            ),
          ]),
        ),
      ),
    );
    setOutputTab("response");
  }
  function update(key: string, value: unknown, remove = false) {
    const next = { ...body };
    if (remove) {
      delete next[key];
      setInvalidFields((old) => old.filter((item) => item !== key));
    } else next[key] = value;
    setInput(format(next));
    setError("");
  }
  async function run(name: string, params: Record<string, unknown>) {
    const result = await execute(name, params);
    setConfirm(undefined);
    setOutputTab("response");
    if (!result.ok) setError(result.error);
  }
  function submit() {
    if (!valid || invalidFields.length) {
      setError("请先修正未完成的 JSON 参数");
      return;
    }
    setError("");
    if (action?.mutating) setConfirm({ action: selected, params: body });
    else void run(selected, body);
  }
  return (
    <div className="explorer">
      <section className="card api-directory">
        <div className="card-heading">
          <h2>接口目录</h2>
          <Badge>{catalog?.actions.length || 0}</Badge>
        </div>
        <label className="search-field">
          <Search size={15} />
          <input
            aria-label="搜索接口"
            placeholder="搜索名称或功能"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <Select
          label="接口分类"
          value={group}
          onChange={setGroup}
          options={["全部分类", ...groups].map((name) => ({
            value: name,
            label: name,
          }))}
        />
        <div className="api-list">
          {filtered.map((item) => (
            <button
              key={item.name}
              className={selected === item.name ? "selected" : ""}
              onClick={() => choose(item)}
            >
              <span>
                <strong>{actionLabel(item)}</strong>
                <small>{item.name}</small>
              </span>
              <i
                className={item.mutating ? "write-dot" : "read-dot"}
                title={item.mutating ? "写操作" : "读取"}
              />
            </button>
          ))}
          {!filtered.length && (
            <Empty title={catalog ? "未找到接口" : "连接后加载接口目录"} />
          )}
        </div>
        <div className="directory-footer">
          {filtered.length} 个接口{" "}
          <span>
            <i className="read-dot" />
            读取
            <i className="write-dot" />
            写入
          </span>
        </div>
      </section>
      <div className="api-workspace">
        <section className="card api-request">
          <div className="card-heading">
            <div>
              <h2>
                {action && actionLabel(action) !== action.name
                  ? actionLabel(action)
                  : "请求编辑器"}
              </h2>
              <p className="mono accent-text">{selected}</p>
            </div>
            <Badge tone={action?.mutating ? "warning" : "success"}>
              {action?.mutating ? "写操作" : "读取"}
            </Badge>
          </div>
          <div className="tabs">
            <button
              className={mode === "form" ? "active" : ""}
              onClick={() => setMode("form")}
            >
              <ListFilter size={15} />
              表单参数
            </button>
            <button
              className={mode === "json" ? "active" : ""}
              onClick={() => {
                setMode("json");
                setInvalidFields([]);
              }}
            >
              <Braces size={15} />
              JSON
            </button>
          </div>
          {mode === "json" ? (
            <textarea
              className="json-editor"
              aria-label="请求参数"
              spellCheck={false}
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                setError("");
                setInvalidFields([]);
              }}
            />
          ) : !valid ? (
            <div className="inline-error">
              JSON 无法解析，请切回 JSON 模式修正。
            </div>
          ) : (
            <div className="parameter-form">
              {Object.entries(schema.properties || {}).map(([key, raw]) => {
                const field = resolve(raw, catalog);
                const required = schema.required?.includes(key);
                const included = key in body;
                const structured =
                  ["object", "array"].includes(field.type || "") ||
                  !!field.properties;
                return (
                  <div className="parameter" key={`${selected}:${key}`}>
                    <div className="parameter-label">
                      <label htmlFor={`param-${key}`}>
                        <code>{key}</code>
                        <span>{field.type || "value"}</span>
                        {required && <em>必填</em>}
                      </label>
                      {!required && (
                        <label className="check-label">
                          <input
                            type="checkbox"
                            aria-label={`包含参数 ${key}`}
                            checked={included}
                            onChange={(e) =>
                              update(
                                key,
                                initialValue(key, field, target),
                                !e.target.checked,
                              )
                            }
                          />
                          传入
                        </label>
                      )}
                    </div>
                    {included &&
                      (field.enum ? (
                        <Select
                          id={`param-${key}`}
                          label={key}
                          value={String(body[key])}
                          onChange={(selected) =>
                            update(
                              key,
                              field.enum!.find(
                                (value) => String(value) === selected,
                              ),
                            )
                          }
                          options={field.enum.map((value) => ({
                            value: String(value),
                            label: String(value),
                          }))}
                        />
                      ) : field.type === "boolean" ? (
                        <Select
                          id={`param-${key}`}
                          label={key}
                          value={String(body[key])}
                          onChange={(selected) =>
                            update(key, selected === "true")
                          }
                          options={[
                            { value: "true", label: "true" },
                            { value: "false", label: "false" },
                          ]}
                        />
                      ) : structured ? (
                        <StructuredInput
                          id={`param-${key}`}
                          value={body[key]}
                          onChange={(value) => update(key, value)}
                          onValidity={(valid) =>
                            setInvalidFields((old) =>
                              valid
                                ? old.filter((item) => item !== key)
                                : [...new Set([...old, key])],
                            )
                          }
                        />
                      ) : (
                        <input
                          id={`param-${key}`}
                          type={
                            ["integer", "number"].includes(field.type || "")
                              ? "number"
                              : "text"
                          }
                          value={
                            body[key] === null ? "" : String(body[key] ?? "")
                          }
                          placeholder={raw.description || field.description}
                          onChange={(e) =>
                            update(
                              key,
                              ["integer", "number"].includes(field.type || "")
                                ? e.target.value === ""
                                  ? ""
                                  : Number(e.target.value)
                                : e.target.value,
                            )
                          }
                        />
                      ))}
                    {(raw.description || field.description) && (
                      <small>{raw.description || field.description}</small>
                    )}
                  </div>
                );
              })}
              {!Object.keys(schema.properties || {}).length && (
                <div className="no-params">
                  <Check size={17} />
                  此接口无需参数
                </div>
              )}
            </div>
          )}
          {error && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
          <div className="request-footer">
            <small>调用只提交一次，超时不会重发。</small>
            <button
              className="primary"
              disabled={
                disabled || !action || !valid || invalidFields.length > 0
              }
              onClick={submit}
            >
              <Play size={15} />
              执行调用
            </button>
          </div>
        </section>
        <section className="card api-response">
          <div className="tabs">
            <button
              className={outputTab === "response" ? "active" : ""}
              onClick={() => setOutputTab("response")}
            >
              <Code2 size={15} />
              响应结果
            </button>
            <button
              className={outputTab === "history" ? "active" : ""}
              onClick={() => setOutputTab("history")}
            >
              <History size={15} />
              调用记录 <span>{activities.length}</span>
            </button>
            <button
              className={outputTab === "schema" ? "active" : ""}
              onClick={() => setOutputTab("schema")}
            >
              接口类型
            </button>
          </div>
          {outputTab === "response" &&
            (recent ? (
              <>
                <div className="response-meta">
                  <Badge tone={recent.ok ? "success" : "danger"}>
                    {recent.ok ? "调用成功" : "调用失败"}
                  </Badge>
                  <span>{recent.duration} ms</span>
                  <time>{new Date(recent.at).toLocaleTimeString()}</time>
                </div>
                <JsonView
                  value={
                    recent.ok
                      ? recent.data
                      : {
                          message: recent.error,
                          ...(recent.data !== undefined
                            ? { data: recent.data }
                            : {}),
                        }
                  }
                  label="调用结果"
                />
              </>
            ) : (
              <Empty title="等待接口响应">执行后在这里查看返回值和耗时。</Empty>
            ))}
          {outputTab === "history" &&
            (activities.length ? (
              <div className="call-history">
                {activities.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => {
                      const found = catalog?.actions.find(
                        (a) => a.name === item.action,
                      );
                      if (found) {
                        choose(found);
                        setInput(format(item.params));
                      }
                    }}
                  >
                    <Badge tone={item.ok ? "success" : "danger"}>
                      {item.ok ? "成功" : "失败"}
                    </Badge>
                    <code>{item.action}</code>
                    <small>{item.duration} ms</small>
                    <span>填入参数</span>
                  </button>
                ))}
              </div>
            ) : (
              <Empty title="暂无调用记录" />
            ))}
          {outputTab === "schema" && (
            <>
              <JsonView value={schema} label="参数类型" />
              <JsonView
                value={resolve(action?.response, catalog)}
                label="返回类型"
              />
            </>
          )}
        </section>
      </div>
      {confirm && (
        <Modal
          title="确认执行写操作"
          busy={busy}
          onClose={() => setConfirm(undefined)}
        >
          <p className="modal-context">{confirm.action}</p>
          <JsonView value={confirm.params} label="本次提交参数" />
          <p className="muted">请确认目标和参数；执行后以实际回执为准。</p>
          <div className="modal-actions">
            <button disabled={busy} onClick={() => setConfirm(undefined)}>
              取消
            </button>
            <button
              className="primary"
              disabled={disabled}
              onClick={() => void run(confirm.action, confirm.params)}
            >
              确认执行
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function StructuredInput({
  id,
  value,
  onChange,
  onValidity,
}: {
  id: string;
  value: unknown;
  onChange: (value: unknown) => void;
  onValidity: (valid: boolean) => void;
}) {
  const [draft, setDraft] = useState(format(value));
  const [error, setError] = useState("");
  const serialized = format(value);
  useEffect(() => {
    setDraft(serialized);
    setError("");
    onValidity(true);
  }, [serialized]);
  return (
    <>
      <textarea
        id={id}
        className="json-editor small"
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          try {
            onChange(JSON.parse(e.target.value));
            onValidity(true);
            setError("");
          } catch {
            onValidity(false);
            setError("请填写有效 JSON，修正前不能提交。");
          }
        }}
      />
      {error && <small className="inline-error">{error}</small>}
    </>
  );
}

export function Events({
  events,
  onClear,
  listening,
  connected,
}: {
  events: ReceivedEvent[];
  onClear: () => void;
  listening: boolean;
  connected: boolean;
}) {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [frozen, setFrozen] = useState<ReceivedEvent[]>();
  const [open, setOpen] = useState<number>();
  const visible = frozen ?? events;
  const indexed = useMemo(
    () =>
      visible.map((event) => ({
        ...event,
        search: format(event.packet).toLowerCase(),
      })),
    [visible],
  );
  const filtered = indexed.filter(
    (event) =>
      (kind === "all" || event.packet.post_type === kind) &&
      event.search.includes(query.toLowerCase()),
  );
  const kinds = [
    ["all", "全部"],
    ["message", "消息"],
    ["notice", "通知"],
    ["request", "请求"],
    ["meta_event", "元事件"],
    ["quitbot", "扩展事件"],
  ];
  return (
    <section className="card event-card">
      <div className="card-heading">
        <div>
          <h2>
            实时事件
            <Badge tone={frozen ? "warning" : listening ? "success" : ""}>
              {frozen
                ? "展示已暂停"
                : listening
                  ? "正在接收"
                  : connected
                    ? "监听未启动"
                    : "连接已断开"}
            </Badge>
          </h2>
          <p className="muted">
            保留最近 200 条；暂停仅冻结本页展示，监听继续运行。
          </p>
        </div>
        <div className="button-group">
          <button onClick={() => setFrozen(frozen ? undefined : [...events])}>
            {frozen ? <Play size={15} /> : <Pause size={15} />}
            {frozen ? "恢复展示" : "暂停展示"}
          </button>
          <button
            aria-label="清空本页事件"
            title="清空本页"
            className="icon-button"
            onClick={() => {
              onClear();
              setFrozen(undefined);
              setOpen(undefined);
            }}
          >
            <Trash2 size={16} />
          </button>
        </div>
      </div>
      <div className="event-toolbar">
        <div className="filter-pills">
          {kinds.map(([key, label]) => (
            <button
              className={kind === key ? "active" : ""}
              key={key}
              onClick={() => setKind(key)}
            >
              {label}
              {key === "all" && <span>{visible.length}</span>}
            </button>
          ))}
        </div>
        <label className="search-field">
          <Search size={15} />
          <input
            aria-label="搜索事件"
            placeholder="搜索事件内容或账号…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
      </div>
      <div className="event-stream">
        {filtered.length ? (
          filtered.map((event) => (
            <div className="event-entry" key={event.id}>
              <button
                className="event-summary"
                onClick={() =>
                  setOpen(open === event.id ? undefined : event.id)
                }
                aria-expanded={open === event.id}
              >
                <ChevronDown
                  size={14}
                  className={open === event.id ? "expanded" : ""}
                />
                <time>
                  {new Date(
                    typeof event.packet.time === "number"
                      ? event.packet.time * 1000
                      : event.at,
                  ).toLocaleTimeString()}
                </time>
                <Badge
                  tone={event.packet.post_type === "message" ? "accent" : ""}
                >
                  {String(event.packet.post_type)}
                </Badge>
                <strong>
                  {String(
                    event.packet.notice_type ||
                      event.packet.request_type ||
                      event.packet.message_type ||
                      event.packet.meta_event_type ||
                      event.packet.detail_type ||
                      "event",
                  )}
                </strong>
                <span className="event-preview">
                  {String(
                    event.packet.raw_message ||
                      event.packet.message ||
                      event.packet.comment ||
                      "",
                  )}
                </span>
              </button>
              {open === event.id && (
                <JsonView value={event.packet} label="事件数据" />
              )}
            </div>
          ))
        ) : (
          <Empty title={visible.length ? "没有符合条件的事件" : "等待新事件"}>
            {connected
              ? "启动监听后，消息、通知与请求会显示在这里。"
              : "请先连接账号服务。"}
          </Empty>
        )}
      </div>
      <div className="table-footer">
        <span>显示 {filtered.length} 条</span>
        <span>断线期间不补发 · 切换账号清空</span>
      </div>
    </section>
  );
}
