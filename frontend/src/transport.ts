import type { operations, components } from "./generated/api";
import type { components as management } from "./generated/management";

export type SessionInfo = management["schemas"]["SessionInfo"];
export type SessionCredential = management["schemas"]["SessionCredential"];

export type ActionName = {
  [K in keyof operations]: operations[K] extends { requestBody: unknown }
    ? K
    : never;
}[keyof operations];
export type Params<K extends ActionName> = operations[K] extends {
  requestBody: { content: { "application/json": infer P } };
}
  ? P
  : never;
export type Result<K extends ActionName> = operations[K] extends {
  responses: { 200: { content: { "application/json": { data?: infer R } } } };
}
  ? Exclude<R, null | components["schemas"]["UnknownOutcome"]>
  : never;
export type EventPacket = Record<string, unknown>;

export class RpcError extends Error {
  constructor(
    message: string,
    public retcode: number,
    public data?: unknown,
  ) {
    super(message);
  }
}

/** One connection is one account. Disconnects/timeouts never trigger resends. */
export class Connection {
  private socket?: WebSocket;
  private serial = 0;
  private pending = new Map<
    string,
    {
      resolve: (value: unknown) => void;
      reject: (error: Error) => void;
      timer: ReturnType<typeof setTimeout>;
    }
  >();
  onEvent: (packet: EventPacket) => void = () => {};
  onClose: (reason: string) => void = () => {};

  async connect(
    url: string,
    auth: { accessToken: string } | { password: string },
  ): Promise<SessionInfo & { access_token?: string }> {
    this.close();
    const parsed = new URL(url);
    if (!["ws:", "wss:"].includes(parsed.protocol))
      throw new Error("请输入 ws:// 或 wss:// 地址");
    const socket = (this.socket = new WebSocket(url));
    socket.onmessage = ({ data }) => {
      if (this.socket !== socket) return;
      let packet: Record<string, unknown>;
      try {
        packet = JSON.parse(data);
      } catch {
        return;
      }
      if (typeof packet.echo === "string" && this.pending.has(packet.echo)) {
        const item = this.pending.get(packet.echo)!;
        clearTimeout(item.timer);
        this.pending.delete(packet.echo);
        if (packet.status === "ok") item.resolve(packet.data);
        else
          item.reject(
            new RpcError(
              String(packet.message || packet.wording || "调用失败"),
              Number(packet.retcode),
              packet.data,
            ),
          );
      } else if (packet.post_type) this.onEvent(packet);
    };
    socket.onclose = ({ code, reason }) => {
      if (this.socket !== socket) return;
      this.socket = undefined;
      this.rejectPending(
        "连接已断开；在途操作的结果可能未知，请先查询状态再决定是否重试。",
      );
      this.onClose(`${code}${reason ? " · " + reason : ""}`);
    };
    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => {
        socket.close();
        reject(new Error("连接超时"));
      }, 8000);
      socket.onopen = () => {
        clearTimeout(timeout);
        resolve();
      };
      socket.onerror = () => {
        clearTimeout(timeout);
        reject(new Error("无法连接账号服务，请检查地址和服务状态"));
      };
    });
    try {
      if (this.socket !== socket) throw new Error("连接已取消");
      return (await this.callRaw(
        "password" in auth ? "x_login" : "x_authenticate",
        "password" in auth
          ? { password: auth.password }
          : { access_token: auth.accessToken },
        10000,
      )) as SessionInfo & { access_token?: string };
    } catch (error) {
      if (this.socket === socket) this.close();
      throw error;
    }
  }

  call<K extends ActionName>(
    action: K,
    params: Params<K>,
    timeout = 60000,
  ): Promise<Result<K>> {
    return this.callRaw(action, params, timeout) as Promise<Result<K>>;
  }

  callRaw(
    action: string,
    params: unknown = {},
    timeout = 60000,
  ): Promise<unknown> {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN)
      return Promise.reject(new Error("请先连接账号服务"));
    const echo = `web-${++this.serial}`;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(echo);
        reject(
          new RpcError(
            "等待回执超时，操作可能已经提交。请检查结果，勿直接重复提交。",
            101,
          ),
        );
      }, timeout);
      this.pending.set(echo, { resolve, reject, timer });
      try {
        this.socket!.send(JSON.stringify({ action, params, echo }));
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(echo);
        reject(error);
      }
    });
  }

  private rejectPending(message: string) {
    for (const item of this.pending.values()) {
      clearTimeout(item.timer);
      item.reject(new RpcError(message, 101));
    }
    this.pending.clear();
  }

  close() {
    const old = this.socket;
    this.socket = undefined;
    this.rejectPending("连接已关闭；在途操作结果可能未知。");
    old?.close();
  }
}
