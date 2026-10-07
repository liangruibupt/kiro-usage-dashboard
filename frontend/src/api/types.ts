/** 与 backend/api 响应形状对应的类型。 */

export interface DateRange {
  from?: string;
  to?: string;
  period?: string;
}

export interface SummaryResponse {
  range: { from: string | null; to: string | null };
  kpis: {
    registered_users: number;
    total_users: number;
    active_users: number;
    activation_rate: number;
    daily_active: number;
    daily_active_avg: number;
    daily_active_peak: number;
    total_credits: number;
    overage_users: number;
    est_cost: number;
    avg_credits_per_active: number;
  };
  tiers: { Pro: number; ProPlus: number; ProMax: number; Power: number };
  est_cost_note: string;
}

export interface TrendPoint {
  date: string;
  credits: number;
  active_users: number;
}
export interface TrendResponse {
  series: TrendPoint[];
}

export interface ModelSlice {
  model: string;
  messages: number;
}
export interface ModelsResponse {
  models: ModelSlice[];
}

export interface DepartmentRow {
  department: string;
  users: number;
  total_credits: number;
  avg_credits: number;
}
export interface DepartmentsResponse {
  departments: DepartmentRow[];
}

export interface TopUser {
  email: string;
  display_name: string;
  department: string;
  subscription_tier: string;
  credits_used: number;
}
export interface TopUsersResponse {
  users: TopUser[];
}

export interface UserRow {
  email: string;
  display_name: string;
  department: string;
  subscription_tier: string;
  credits_used: number;
  usage_limit: number;
  balance: number;
  usage_rate: number;
  is_overage: boolean;
  est_cost: number;
  total_messages: number;
  active_days: number;
  status: string;
}
export interface UsersResponse {
  total: number;
  page: number;
  page_size: number;
  sort: string;
  direction: string;
  items: UserRow[];
}

export interface FiltersResponse {
  periods: string[];
  departments: string[];
  models: string[];
}

// 月度报告
export interface ReportRecoRow {
  email: string;
  display_name: string;
  department: string;
  subscription_tier: string;
  credits_used: number;
  usage_limit: number;
  usage_rate: number;
  suggested_tier: string;
}
export interface ReportResponse {
  range: { from: string | null; to: string | null };
  kpis: {
    registered_users: number;
    active_users: number;
    activation_rate: number;
    total_credits: number;
    overage_users: number;
    est_cost: number;
    daily_active_avg: number;
    daily_active_peak: number;
    tiers: { Pro: number; ProPlus: number; ProMax: number; Power: number };
  };
  upgrades: ReportRecoRow[];
  downgrades: ReportRecoRow[];
  est_cost_note: string;
}

// 管理员管理
export interface AdminUser {
  email: string;
  status: string;
  enabled: boolean;
  created_at?: string | null;
}
export interface AdminUsersResponse {
  users: AdminUser[];
}

// 调档申请（Tier requests）
export type TierRequestStatus = "pending" | "completed" | "rejected" | "superseded";
export interface TierRequest {
  id: string;
  requester_email: string;
  requester_name?: string | null; // 由后端用部门表 display_name 补全
  department?: string | null;     // 由后端用部门表 department 补全
  target_tier: string; // 原始键 Pro|ProPlus|ProMax|Power
  reason?: string | null;
  status: TierRequestStatus;
  created_at: string;
  processed_at?: string | null;
  processed_by?: string | null;
  admin_note?: string | null;
}
export interface TierRequestsResponse {
  items: TierRequest[];
}

// 远程 MCP 配置（admins-only，用于调档申请页展示分发步骤）
export interface McpConfigResponse {
  api_endpoint: string;
  mcp_url: string;
  api_key: string;
}
