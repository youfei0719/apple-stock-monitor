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

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
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

export interface Task {
  id: string;
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
  /** 每任务最新状态摘要（后端聚合） */
  summary?: {
    state: StockState;
    available_count: number;
    total_count: number;
    updated_at: string;
    buy_url?: string;
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
  task_id: string;
  part_number: string;
  store: StoreRef;
  state: StockState;
  pickupDisplay?: string;
  storePickEligible?: boolean;
  updated_at: string;
}

export interface Me {
  id: string;
  email: string;
  tier: Tier;
  quota: { push_used: number; push_limit: number; tasks_used: number; tasks_limit: number };
  totp_enabled: boolean;
}

export interface Plan {
  id: Tier;
  name: string;
  price_cny: number;
  period: string;
  refresh_interval_sec: number;
  task_limit: number;
  push_limit: number;
  features: string[];
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
  id: string;
  task_id: string;
  task_name: string;
  part_number: string;
  product_name: string;
  store: StoreRef;
  state: StockState;
  created_at: string;
  buy_url?: string;
}

export interface ReleaseRecord {
  part_number: string;
  product_name: string;
  city: string;
  store_count: number;
  first_seen_at: string;
  last_seen_at: string;
}

export interface RankingItem {
  city: string;
  release_count: number;
  available_count: number;
}

export interface PollStats {
  last_poll_at: string | null;
  success_rate: number;
  avg_response_ms: number;
  total_polls: number;
}

export interface GuideSection {
  title: string;
  body: string;
}

export interface Payment {
  id: string;
  plan: string;
  amount_cny: number;
  created_at: string;
  expires_at: string;
  source: string;
}

/* ---------------- 接口 ---------------- */

export const api = {
  health: () => req<{ status: string; db: boolean; engine: string; version: string }>('/healthz'),

  register: (email: string, password: string) =>
    req<{ id: string; email: string; tier: Tier }>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
  login: (email: string, password: string) =>
    req<{ id: string; email: string; tier: Tier }>('/auth/login', {
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
  updateTask: (id: string, payload: Partial<Task>) =>
    req<Task>(`/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteTask: (id: string) => req<void>(`/tasks/${id}`, { method: 'DELETE' }),

  taskStates: (id: string) => req<StateRow[]>(`/tasks/${id}/states`),

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
  guide: () => req<GuideSection[]>('/guide/purchase'),
  pollStats: () => req<PollStats>('/stats/poll'),

  quota: () => req<Quota>('/quota'),
  plans: () => req<Plan[]>('/plans'),
  payments: () => req<Payment[]>('/payments'),
};
