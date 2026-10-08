/** 看板 API 客户端：自动带 Cognito id token，处理日期范围参数。 */
import { getConfig } from "../config";
import { getIdToken, getAccessToken } from "../auth/auth";
import type {
  AdminUsersResponse,
  DateRange,
  DepartmentsResponse,
  FiltersResponse,
  McpConfigResponse,
  ModelsResponse,
  ReportResponse,
  SummaryResponse,
  TierRequestsResponse,
  TopUsersResponse,
  TrendResponse,
  UsersResponse,
} from "./types";

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  });
  const s = sp.toString();
  return s ? `?${s}` : "";
}

async function get<T>(path: string): Promise<T> {
  const { apiEndpoint } = getConfig();
  const token = getIdToken();
  const resp = await fetch(`${apiEndpoint}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (resp.status === 401 || resp.status === 403) {
    throw new ApiAuthError(resp.status);
  }
  if (!resp.ok) {
    throw new Error(`API ${path} 失败: ${resp.status} ${await resp.text()}`);
  }
  return (await resp.json()) as T;
}

async function mutate<T>(method: "POST" | "DELETE", path: string, body?: unknown): Promise<T> {
  const { apiEndpoint } = getConfig();
  const token = getIdToken();
  const resp = await fetch(`${apiEndpoint}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (resp.status === 401 || resp.status === 403) throw new ApiAuthError(resp.status);
  if (!resp.ok) {
    let detail = await resp.text();
    try {
      detail = JSON.parse(detail).detail ?? detail;
    } catch {
      /* keep text */
    }
    throw new Error(detail || `${method} ${path} 失败: ${resp.status}`);
  }
  return (await resp.json()) as T;
}

export class ApiAuthError extends Error {
  constructor(public status: number) {
    super(`未授权 (${status})`);
  }
}

function rangeParams(r: DateRange): Record<string, string | undefined> {
  return { period: r.period, from: r.from, to: r.to };
}

export const api = {
  summary: (r: DateRange) => get<SummaryResponse>(`/api/summary${qs(rangeParams(r))}`),
  trend: (r: DateRange) => get<TrendResponse>(`/api/trend${qs(rangeParams(r))}`),
  models: (r: DateRange, email?: string) =>
    get<ModelsResponse>(`/api/models${qs({ ...rangeParams(r), email })}`),
  departments: (r: DateRange) =>
    get<DepartmentsResponse>(`/api/departments${qs(rangeParams(r))}`),
  topUsers: (r: DateRange, limit?: number) =>
    get<TopUsersResponse>(`/api/top-users${qs({ ...rangeParams(r), limit })}`),
  users: (
    r: DateRange,
    opts: { department?: string; search?: string; sort?: string; direction?: string; page?: number; page_size?: number }
  ) => get<UsersResponse>(`/api/users${qs({ ...rangeParams(r), ...opts })}`),
  // CSV 导出：需带 Authorization 头，故用 fetch+blob 触发下载（而非 <a href>）。
  // 范围与当前部门 tab + 搜索一致；含零用量用户，不分页。
  usersExportCsv: async (
    r: DateRange,
    opts: { department?: string; search?: string; sort?: string; direction?: string }
  ): Promise<void> => {
    const { apiEndpoint } = getConfig();
    const token = getIdToken();
    const path = `/api/users/export${qs({ ...rangeParams(r), ...opts })}`;
    const resp = await fetch(`${apiEndpoint}${path}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (resp.status === 401 || resp.status === 403) throw new ApiAuthError(resp.status);
    if (!resp.ok) throw new Error(`导出失败: ${resp.status} ${await resp.text()}`);
    // 从 Content-Disposition 解析文件名（优先 filename*=UTF-8''），回退到默认名。
    const cd = resp.headers.get("Content-Disposition") || "";
    let filename = `kiro-users-${new Date().toISOString().slice(0, 10).replace(/-/g, "")}.csv`;
    const star = /filename\*=UTF-8''([^;]+)/i.exec(cd);
    const plain = /filename="?([^";]+)"?/i.exec(cd);
    if (star) filename = decodeURIComponent(star[1]);
    else if (plain) filename = plain[1];
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
  filters: () => get<FiltersResponse>(`/api/filters`),
  report: (r: DateRange) => get<ReportResponse>(`/api/report${qs(rangeParams(r))}`),

  // 管理员管理
  adminListUsers: () => get<AdminUsersResponse>(`/api/admin/users`),
  adminCreateUser: (email: string) =>
    mutate<{ ok: boolean }>("POST", `/api/admin/users`, { email }),
  adminResetPassword: (email: string) =>
    mutate<{ ok: boolean }>("POST", `/api/admin/users/${encodeURIComponent(email)}/reset-password`),
  adminResendInvite: (email: string) =>
    mutate<{ ok: boolean }>("POST", `/api/admin/users/${encodeURIComponent(email)}/resend-invite`),
  adminRemoveUser: (email: string) =>
    mutate<{ ok: boolean }>("DELETE", `/api/admin/users/${encodeURIComponent(email)}`),

  // 调档申请（Tier requests）：提交端点不在前端（由 MCP server 调用）
  tierRequestsList: () => get<TierRequestsResponse>(`/api/tier-requests`),
  tierRequestMcpConfig: () => get<McpConfigResponse>(`/api/tier-requests/mcp-config`),
  tierRequestClose: (id: string, outcome: "completed" | "rejected", note?: string) =>
    mutate<{ ok: boolean }>("POST", `/api/tier-requests/${encodeURIComponent(id)}/close`, {
      outcome,
      admin_note: note,
    }),
  chat: async (prompt: string, sessionId?: string): Promise<{ reply: string; session_id: string }> => {
    const { apiEndpoint } = getConfig();
    const resp = await fetch(`${apiEndpoint}/api/chat`, {
      method: "POST",
      headers: {
        // header 用 id token 过 API Gateway；body 传 access token 给 AgentCore
        Authorization: `Bearer ${getIdToken() ?? ""}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ prompt, session_id: sessionId, access_token: getAccessToken() }),
    });
    if (resp.status === 401 || resp.status === 403) throw new ApiAuthError(resp.status);
    if (!resp.ok) throw new Error(`对话失败: ${resp.status} ${await resp.text()}`);
    return (await resp.json()) as { reply: string; session_id: string };
  },
};
