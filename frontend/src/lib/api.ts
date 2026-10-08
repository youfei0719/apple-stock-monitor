/**
 * API 客户端 —— 严格按 docs/API_CONTRACT.md v1
 * 认证：HttpOnly + Secure + SameSite=Lax Cookie，全请求携带 credentials: 'include'
 * 所有时间：UTC ISO8601。错误格式：{"detail": "...", "code": "..."}
 */

const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api';

export class ApiError extends Error {
  code?: string;
  status: number;
  constructor(status: number, detail: string, code?: string) {
    super(detail);
    this.status = status;
    this.code = code;
  }
}

async function req<T>(path: string, init: RequestInit = {}, absolute = false): Promise<T> {
  let res: Response;
  try {
    // absolute=true 时跳过 API_BASE（如 /healthz 挂在站点根，不在 /api 下）
    res = await fetch(absolute ? path : `${API_BASE}${path}`, {
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
      ...init,
    });
  } catch (e) {
    throw new ApiError(0, e instanceof Error ? `网络异常：${e.message}` : '网络异常');
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const d = (data ?? {}) as { detail?: string; code?: string };
    throw new ApiError(res.status, d.detail ?? `请求失败（${res.status}）`, d.code);
  }
  return data as T;
}

/* ---------------- 类型 ---------------- */

export type StockState =
  | 'available'
  | 'unavailable'
  | 'unknown'
  | 'verifying'
  | 'cooling'
  | 'paused'
  | 'expired';

export type TaskStatusFilter = 'active' | 'expired' | 'all';

export type Tier = 'trial' | 'free' | 'standard' | 'pro';

export interface StoreRef {
  number: string;
  name: string;
  city: string;
  province?: string;
}

/** 后端 notifier 期望 webhooks 为 [{url, platform}]（platform: wecom|dingtalk|feishu） */
export interface WebhookChannel {
  url: string;
  platform: 'wecom' | 'dingtalk' | 'feishu' | string;
}

export interface TaskChannels {
  bark_key?: string;
  webhooks?: WebhookChannel[];
  email?: string;
}

export interface TaskLatestRow {
  state: StockState;
  pickup_display?: string | null;
  store_pick_eligible?: boolean | null;
  pickup_search_quote?: string | null;
  updated_at?: string | null;
}

/** 后端 TaskOut.latest：{stores:{门店号:{part_number:行}}, available_count, total} */
export interface TaskLatest {
  stores: Record<string, Record<string, TaskLatestRow>>;
  available_count: number;
  total: number;
}

export interface Task {
  id: number;
  name: string;
  group?: string;
  category: 'iphone' | 'ipad' | 'mac' | 'watch' | string;
  part_number: string;
  product_name: string;
  color?: string;
  capacity?: string;
  stores: StoreRef[];
  mode: 'instant' | 'confirmed';
  repeat_interval_sec: number | null;
  channels: TaskChannels;
  paused: boolean;
  expires_at: string | null;
  created_at: string;
  /** 每任务最新状态摘要（后端聚合，snake_case） */
  latest?: TaskLatest;
}

/** 任务是否已过期（expires_at 在过去）——expired 展示态的判定依据 */
export function isTaskExpired(task: Task): boolean {
  if (!task.expires_at) return false;
  return new Date(task.expires_at).getTime() < Date.now();
}

/** 从 latest 聚合展示用状态（Home/App 共用）
 *  - expired：任务过期（终态，优先于 paused）
 *  - partialUnknown：unknown 与其他状态混合（如 3 unknown + 2 unavailable），unknown 不再被淹没
 */
export function summarizeTask(task: Task): {
  state: StockState;
  availableCount: number;
  total: number;
  updatedAt?: string;
  partialUnknown: boolean;
} {
  const latest = task.latest;
  const rows: TaskLatestRow[] = [];
  const stores = latest?.stores ?? {};
  for (const parts of Object.values(stores)) {
    for (const row of Object.values(parts ?? {})) rows.push(row);
  }
  let updatedAt: string | undefined;
  for (const r of rows) {
    if (r.updated_at && (!updatedAt || r.updated_at > updatedAt)) updatedAt = r.updated_at;
  }
  const states = rows.map((r) => r.state);
  const hasUnknown = states.includes('unknown');
  let state: StockState = 'unknown';
  if (isTaskExpired(task)) state = 'expired';
  else if (task.paused) state = 'paused';
  else if (states.includes('available')) state = 'available';
  else if (states.includes('verifying')) state = 'verifying';
  else if (states.includes('cooling')) state = 'cooling';
  else if (states.length > 0 && !states.every((s) => s === 'unknown')) state = 'unavailable';
  // 混合 unknown：有 unknown 但不全是 unknown，且展示态不是 available/verifying/expired
  const partialUnknown =
    state !== 'expired' &&
    state !== 'available' &&
    state !== 'verifying' &&
    hasUnknown &&
    states.some((s) => s !== 'unknown');
  return {
    state,
    availableCount: latest?.available_count ?? 0,
    total: latest?.total ?? states.length,
    updatedAt,
    partialUnknown,
  };
}

export interface Product {
  part_number: string;
  name: string;
  color: string;
  capacity: string;
  price_cny: number;
}

export interface StateRow {
  store_number: string;
  part_number: string;
  state: StockState;
  pickup_display?: string | null;
  store_pick_eligible?: boolean | null;
  pickup_search_quote?: string | null;
  confirmed_count?: number;
  last_event_at?: string | null;
  updated_at?: string | null;
}

export interface Me {
  id: number;
  email: string;
  tier: Tier;
  quota: { push_used: number; push_limit: number; tasks_used: number; tasks_limit: number };
  totp_enabled: boolean;
}

export interface Plan {
  tier: Tier;
  name: string;
  price_cny: number;
  tasks_limit: number;
  push_limit: number;
  channels: string[];
  history: boolean;
  priority: boolean;
  refresh_interval_sec: number;
}

export interface Quota {
  tier: Tier;
  push_used: number;
  push_limit: number;
  tasks_used: number;
  tasks_limit: number;
  refresh_interval_sec: number;
  period: string;
  /** 会员到期时间（ISO，购买日 +30 天滚动）；null 表示免费/无到期 */
  tier_expires_at: string | null;
  /** 配额重置时间（ISO，购买日 +30 天滚动） */
  quota_reset_at: string | null;
}

export interface SiteConfig {
  afdian_page_url: string;
}

export interface ChannelHealth {
  key: 'bark' | 'wecom' | 'dingtalk' | 'feishu' | 'email' | string;
  name: string;
  configured: boolean;
  success_rate_7d: number | null;
  last_failure_at: string | null;
  last_failure_reason: string | null;
}

/** /notifications 历史记录：StockEvent 基础上带发送状态与失败原因 */
export interface NotificationRecord extends StockEvent {
  status?: 'sent' | 'failed' | 'skipped' | string | null;
  failure_reason?: string | null;
}

export interface StockEvent {
  id: number;
  task_id: number;
  part_number: string;
  title: string;
  body: string;
  link?: string | null;
  channel: string;
  created_at: string;
}

export interface ReleaseRecord {
  day: string;
  part_number: string;
  events: number;
}

export interface RankingItem {
  city: string;
  events: number;
}

export interface PollStats {
  tasks: number;
  polled_tasks: number;
  success_rate: number | null;
  avg_response_ms: number | null;
  last_poll_at: string | null;
  engine: unknown;
}

export interface PurchaseGuide {
  title: string;
  steps: string[];
}

export interface Payment {
  id: number;
  order_id: string;
  plan: string;
  amount_cny: number;
  tier_from: string;
  tier_to: string;
  status: string;
  created_at: string;
}

/* ---------------- 接口 ---------------- */

export const api = {
  // healthz 挂在站点根（/healthz），不在 /api 下
  health: () =>
    req<{ status: string; db: boolean; engine: string; version: string }>('/healthz', {}, true),

  register: (email: string, password: string) =>
    req<{ id: number; email: string; tier: Tier; totp_enabled: boolean }>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
  // 登录成功：后端返回 {ok:true, totp_required:bool} + Set-Cookie(session_token)
  login: (email: string, password: string) =>
    req<{ ok: boolean; totp_required: boolean }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
  verifyEmail: (email: string, code: string) =>
    req<{ ok: boolean }>('/auth/verify-email', {
      method: 'POST',
      body: JSON.stringify({ email, code }),
    }),
  /** 重发验证码：后端若未实现该接口（404），调用方按"重新注册获取验证码"兜底 */
  resendCode: (email: string) =>
    req<{ ok: boolean }>('/auth/resend-code', {
      method: 'POST',
      body: JSON.stringify({ email }),
    }),
  logout: () => req<void>('/auth/logout', { method: 'POST' }),
  me: () => req<Me>('/me'),

  tasks: (status: TaskStatusFilter = 'all') => {
    const q = status === 'all' ? '' : `?status=${encodeURIComponent(status)}`;
    return req<Task[]>(`/tasks${q}`);
  },
  task: (id: string | number) => req<Task>(`/tasks/${id}`),
  renewTask: (id: string | number) =>
    req<{ ok: boolean; expires_at: string }>(`/tasks/${id}/renew`, { method: 'POST' }),
  createTask: (payload: Partial<Task>) =>
    req<Task>('/tasks', { method: 'POST', body: JSON.stringify(payload) }),
  batchTasks: (payload: { part_numbers: string[]; store_numbers: string[]; name_template: string }) =>
    req<Task[]>('/tasks/batch', { method: 'POST', body: JSON.stringify(payload) }),
  updateTask: (id: string | number, payload: Partial<Task>) =>
    req<Task>(`/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteTask: (id: string | number) => req<void>(`/tasks/${id}`, { method: 'DELETE' }),

  taskStates: (id: string | number) => req<StateRow[]>(`/tasks/${id}/states`),

  stores: (refresh = 0) => req<StoreRef[]>(`/catalog/stores?refresh=${refresh}`),
  products: (category: string) => req<Product[]>(`/catalog/products?category=${category}`),

  notifyTest: (channel: string, target: string) =>
    req<{ ok: boolean; message?: string }>('/notify/test', {
      method: 'POST',
      body: JSON.stringify({ channel, target }),
    }),
  notifications: (task_id?: string | number) =>
    req<NotificationRecord[]>(
      `/notifications${task_id ? `?task_id=${encodeURIComponent(String(task_id))}` : ''}`,
    ),
  channelHealth: () =>
    req<{ channels: ChannelHealth[] }>('/notify/channels/health'),

  siteConfig: () => req<SiteConfig>('/site-config'),

  events: (params: { part_number?: string; store?: string; days?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.part_number) q.set('part_number', params.part_number);
    if (params.store) q.set('store', params.store);
    q.set('days', String(params.days ?? 30));
    return req<StockEvent[]>(`/history/events?${q.toString()}`);
  },
  releases: (days = 7) => req<ReleaseRecord[]>(`/history/releases?days=${days}`),
  /** 后端返回 {"scope": "personal", items|ranking: [...]}；兼容旧的纯数组形状 */
  ranking: async (days = 1): Promise<RankingItem[]> => {
    const data = await req<
      RankingItem[] | { scope?: string; items?: RankingItem[]; ranking?: RankingItem[] }
    >(`/analytics/ranking?days=${days}`);
    if (Array.isArray(data)) return data;
    return data.items ?? data.ranking ?? [];
  },
  analyticsOverview: () => req<Record<string, unknown>>('/analytics/overview'),
  guide: () => req<PurchaseGuide>('/guide/purchase'),
  pollStats: () => req<PollStats>('/stats/poll'),

  quota: () => req<Quota>('/quota'),
  plans: () => req<Plan[]>('/plans'),
  payments: () => req<Payment[]>('/payments'),
};
