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
  | 'paused';

export type Tier = 'trial' | 'free' | 'standard' | 'pro';

export interface StoreRef {
  number: string;
  name: string;
  city: string;
  province?: string;
}

export interface TaskChannels {
  bark_key?: string;
  webhooks?: string[];
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

/** 从 latest 聚合展示用状态（Home/App 共用） */
export function summarizeTask(task: Task): {
  state: StockState;
  availableCount: number;
  total: number;
  updatedAt?: string;
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
  let state: StockState = 'unknown';
  if (task.paused) state = 'paused';
  else if (states.includes('available')) state = 'available';
  else if (states.includes('verifying')) state = 'verifying';
  else if (states.includes('cooling')) state = 'cooling';
  else if (states.length > 0 && !states.every((s) => s === 'unknown')) state = 'unavailable';
  return {
    state,
    availableCount: latest?.available_count ?? 0,
    total: latest?.total ?? states.length,
    updatedAt,
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
  logout: () => req<void>('/auth/logout', { method: 'POST' }),
  me: () => req<Me>('/me'),

  tasks: () => req<Task[]>('/tasks'),
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
  notifications: (task_id?: string) =>
    req<StockEvent[]>(`/notifications${task_id ? `?task_id=${encodeURIComponent(task_id)}` : ''}`),

  events: (params: { part_number?: string; store?: string; days?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.part_number) q.set('part_number', params.part_number);
    if (params.store) q.set('store', params.store);
    q.set('days', String(params.days ?? 30));
    return req<StockEvent[]>(`/history/events?${q.toString()}`);
  },
  releases: (days = 7) => req<ReleaseRecord[]>(`/history/releases?days=${days}`),
  ranking: (days = 1) => req<RankingItem[]>(`/analytics/ranking?days=${days}`),
  analyticsOverview: () => req<Record<string, unknown>>('/analytics/overview'),
  guide: () => req<PurchaseGuide>('/guide/purchase'),
  pollStats: () => req<PollStats>('/stats/poll'),

  quota: () => req<Quota>('/quota'),
  plans: () => req<Plan[]>('/plans'),
  payments: () => req<Payment[]>('/payments'),
};
