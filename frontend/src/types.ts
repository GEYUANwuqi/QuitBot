import type { components } from "./generated/api";
import type { components as management } from "./generated/management";
import type { EventPacket } from "./transport";

export type Status = components["schemas"]["RuntimeStatus"];
export type Target = components["schemas"]["Target"];
export type Schema = {
  $ref?: string;
  type?: string;
  title?: string;
  description?: string;
  properties?: Record<string, Schema>;
  required?: string[];
  enum?: unknown[];
  default?: unknown;
  anyOf?: Schema[];
  items?: Schema;
};
export type CatalogAction = {
  name: string;
  summary: string;
  mutating: boolean;
  params: Schema;
  response: Schema;
};
export type Catalog = {
  version: string;
  actions: CatalogAction[];
  schemas: Record<string, Schema>;
};
export type Outcome =
  { ok: true; data: unknown } | { ok: false; error: string };
export type Execute = (
  action: string,
  params?: Record<string, unknown>,
) => Promise<Outcome>;
export type Profile = {
  id: string;
  name: string;
  url: string;
  avatar?: string | null;
  docsUrl?: string;
};
export function accountProfile(
  account: management["schemas"]["DiscoveredWindow"],
): Profile {
  return {
    id: account.account_id!,
    name: account.nickname,
    avatar: account.avatar,
    url: `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/accounts/${account.account_id}/ws`,
  };
}
export type Activity = {
  id: number;
  action: string;
  params: Record<string, unknown>;
  at: number;
  duration: number;
  ok: boolean;
  data?: unknown;
  error?: string;
};
export type ReceivedEvent = { id: number; at: number; packet: EventPacket };
export type Page =
  "overview" | "connections" | "accounts" | "settings" | "api" | "events";
export const format = (value: unknown) => JSON.stringify(value, null, 2);
const labels: Record<string, string> = {
  send_group_msg: "发送群消息",
  send_private_msg: "发送私聊消息",
  delete_msg: "撤回消息",
  x_send_file: "发送文件",
  x_get_group_file_entries: "读取群文件目录",
  x_create_group_folder: "新建群文件夹",
  x_upload_group_file: "上传群文件",
  x_rename_group_file: "重命名群文件",
  x_rename_group_folder: "重命名群文件夹",
  x_delete_group_file: "删除群文件",
  x_delete_group_folder: "删除空文件夹",
  x_move_group_file: "移动群文件",
  x_download_group_file: "下载群文件",
  x_get_group_notices: "读取群公告",
  x_publish_group_notice: "发布群公告",
  x_edit_group_notice: "编辑群公告",
  x_delete_group_notice: "删除群公告",
  x_get_group_notice_pinned: "读取公告置顶状态",
  x_set_group_notice_pinned: "设置公告置顶",
};
export const actionLabel = (action: CatalogAction) =>
  labels[action.name] || action.summary;
export const listenerLabel = (status?: Status) =>
  status
    ? {
        running: "监听中",
        starting: "启动中",
        failed: "监听中断",
        stopped: "未启动",
      }[status.listener_state]
    : "未连接";

export const reverseLabel = (status?: Status["reverse_ws"]) =>
  ({
    disabled: "未启用",
    connecting: "连接中",
    connected: "已连接",
    reconnecting: "等待重连",
    stopped: "已停止",
  })[status?.state || "disabled"];

export function saved(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}
export function persist(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* The current session remains usable without storage. */
  }
}
export function actionGroup(name: string): string {
  if (
    /runtime|api_catalog|^x_(connect|disconnect|start_listener|stop_listener)$|^get_(status|version_info|login_info)$/.test(
      name,
    )
  )
    return "服务与状态";
  if (/file|folder/.test(name)) return "文件管理";
  if (/notice|essence/.test(name)) return "群公告与精华";
  if (/msg|message|history|reaction|draft|sticker/.test(name))
    return "消息与互动";
  if (/group|member|admin|ban/.test(name)) return "群与成员";
  return "联系人与其他";
}
