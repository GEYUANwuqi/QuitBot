import {
  Activity as ActivityIcon,
  Cable,
  Check,
  Code2,
  MessageSquare,
  Radio,
  Users,
} from "lucide-react";
import type { Activity, Catalog, Status } from "./types";
import { listenerLabel, reverseLabel } from "./types";
import { Badge, Empty } from "./ui";

export default function Overview({
  connected,
  status,
  catalog,
  endpoint,
  activities,
  eventCount,
}: {
  connected: boolean;
  status?: Status;
  catalog?: Catalog;
  endpoint: string;
  activities: Activity[];
  eventCount: number;
}) {
  return (
    <>
      <div className="stats">
        <section className="stat">
          <span className="tile-icon purple">
            <Cable size={20} />
          </span>
          <span>服务连接</span>
          <strong>{connected ? "已连接" : "未连接"}</strong>
          <small>
            {connected ? "WebSocket 双向连接" : "选择本机 QQ 账号以开始"}
          </small>
        </section>
        <section className="stat">
          <span className="tile-icon blue">
            <Users size={20} />
          </span>
          <span>已配置会话</span>
          <strong>
            {status?.targets.length ?? "—"}
            <em>个</em>
          </strong>
          <small>
            {status
              ? `${status.targets.filter((t) => t.kind === "group").length} 个群聊 · ${status.targets.filter((t) => t.kind === "private").length} 个私聊`
              : "连接后读取会话范围"}
          </small>
        </section>
        <section className="stat">
          <span className="tile-icon amber">
            <Code2 size={20} />
          </span>
          <span>可调用接口</span>
          <strong>
            {catalog?.actions.length ?? "—"}
            <em>个</em>
          </strong>
          <small>以当前服务返回的目录为准</small>
        </section>
        <section className="stat">
          <span className="tile-icon green">
            <Radio size={20} />
          </span>
          <span>事件监听</span>
          <strong>{listenerLabel(status)}</strong>
          <small>本次连接已保留 {eventCount} 条事件</small>
        </section>
      </div>
      <div className="overview-grid">
        <section className="card account-card">
          <div className="card-heading">
            <h2>当前账号</h2>
            <span className="muted">状态自动更新</span>
          </div>
          <div className="account-identity">
            <span className="account-avatar">{status?.label?.[0] || "Q"}</span>
            <div>
              <h3>{status?.label || "等待连接账号"}</h3>
              <p className="mono">
                {status?.account_id
                  ? `QQ ${status.account_id}`
                  : "一个账号，一个独立连接"}
              </p>
            </div>
            <Badge tone={status?.connected ? "success" : ""}>
              {status?.connected ? "QQ 已绑定" : "QQ 未绑定"}
            </Badge>
          </div>
          <div className="endpoint-line">
            <Cable size={14} />
            <span className="mono">
              {connected ? endpoint : "尚未选择 QQ 账号"}
            </span>
          </div>
          <div className="setup-steps">
            <div className={connected ? "done" : ""}>
              <span>{connected ? <Check size={15} /> : "1"}</span>
              <strong>选择账号</strong>
              <small>探查后连接</small>
            </div>
            <div className={status?.connected ? "done" : ""}>
              <span>{status?.connected ? <Check size={15} /> : "2"}</span>
              <strong>身份核验</strong>
              <small>核验当前账号</small>
            </div>
            <div className={status?.listening ? "done" : ""}>
              <span>{status?.listening ? <Check size={15} /> : "3"}</span>
              <strong>事件监听</strong>
              <small>接收消息与通知</small>
            </div>
          </div>
          {status?.reverse_ws?.enabled && (
            <div className="endpoint-line">
              <Cable size={14} />
              <span>反向 WS · {reverseLabel(status.reverse_ws)}</span>
              <span className="mono">{status.reverse_ws.url}</span>
            </div>
          )}
          {status?.reverse_ws?.last_error && (
            <p className="inline-error">{status.reverse_ws.last_error}</p>
          )}
          {connected && !status?.targets.length && (
            <p className="muted">
              先在账号设置中添加要监听的群聊或私聊，保存后即可启动监听。
            </p>
          )}
          {status?.last_error && (
            <p className="inline-error">{status.last_error}</p>
          )}
        </section>
        <section className="card">
          <div className="card-heading">
            <h2>
              配置会话{" "}
              <span className="muted">{status?.targets.length ?? 0}</span>
            </h2>
            <Badge>当前账号</Badge>
          </div>
          {status?.targets.length ? (
            <div className="target-list">
              {status.targets.map((target) => (
                <div
                  className="target-row"
                  key={target.kind + target.id}
                >
                  <span className={`target-avatar ${target.kind}`}>
                    {target.kind === "group" ? (
                      <Users size={18} />
                    ) : (
                      <MessageSquare size={18} />
                    )}
                  </span>
                  <span>
                    <strong>{target.name}</strong>
                    <small>
                      {target.kind === "group" ? "群聊" : "私聊"} · {target.id}
                    </small>
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <Empty title="还没有可用会话">
              在账号设置的「监听范围」中添加允许操作的群聊与私聊。
            </Empty>
          )}
        </section>
        <section className="card overview-activity">
          <div className="card-heading">
            <h2>最近调用</h2>
            <span className="muted">最近 5 次</span>
          </div>
          {activities.length ? (
            <div className="activity-list">
              {activities.slice(0, 5).map((item) => (
                <div className="activity-row" key={item.id}>
                  <span className={`activity-dot ${item.ok ? "" : "failed"}`} />
                  <div>
                    <strong className="mono">{item.action}</strong>
                    <small>
                      {new Date(item.at).toLocaleTimeString()} · {item.duration}{" "}
                      ms
                    </small>
                  </div>
                  <Badge tone={item.ok ? "success" : "danger"}>
                    {item.ok ? "成功" : "失败"}
                  </Badge>
                </div>
              ))}
            </div>
          ) : (
            <Empty title="暂无调用记录">
              接口调试的调用结果会显示在这里。
            </Empty>
          )}
        </section>
      </div>
      <div className="info-strip">
        <ActivityIcon size={17} />
        <p>
          服务连接、QQ 绑定和事件监听分别显示。QQ
          绑定状态表示窗口已核验，不等同于实时在线状态。
        </p>
      </div>
    </>
  );
}
