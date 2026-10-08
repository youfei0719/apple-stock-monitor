/**
 * API 客户端 —— 严格按 docs/API_CONTRACT.md v1
 * 认证：HttpOnly + Secure + SameSite=Lax Cookie，全请求携带 credentials: 'include'
 * 所有时间：UTC ISO8601。错误格式：{"detail": "...", "code": "..."}
 */

const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api';

/**
 * 匿名体验设备标识（F4）：后端 X-Device-Id 匿名链路用（tasks.py：无会话时可创建 1 个任务，
 * 配额按 device 逐月计数；auth.py 登录/注册时读同一 header 自动认领匿名任务）。
 * 登录用户也带上该 header——无副作用，是任务迁移（claim）的关键。
 */
const DEVICE_KEY = 'stockmon.device_id';

function newDeviceId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `dev-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** localStorage/sessionStorage 都不可用时的进程内回退 id（页面生命周期内稳定） */
let memDeviceId: string | undefined;

/** F-N6：绝不回退到固定 'dev-anon'——localStorage 被禁用时所有用户会串号，
 * 互相覆盖匿名任务与配额；降级链：localStorage → sessionStorage → 内存随机 id */
/* R7-UI：去掉死导出——全库只有本文件 req() 内部使用（X-Device-Id 头），
 * 无外部引用，改为模块内私有函数 */
function getDeviceId(): string {
  try {
    let id = localStorage.getItem(DEVICE_KEY);
    if (!id) {
      id = newDeviceId();
      localStorage.setItem(DEVICE_KEY, id);
    }
    return id;
  } catch {
    /* localStorage 被禁用 → 降级 sessionStorage */
  }
  try {
    let id = sessionStorage.getItem(DEVICE_KEY);
    if (!id) {
      id = newDeviceId();
      sessionStorage.setItem(DEVICE_KEY, id);
    }
    return id;
  } catch {
    /* sessionStorage 也不可用 → 内存 id */
  }
  return (memDeviceId ??= newDeviceId());
}

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
      headers: {
        'Content-Type': 'application/json',
        'X-Device-Id': getDeviceId(),
        ...(init.headers ?? {}),
      },
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

/** 任务是否已过期（expires_at 在过去）——expired 展示态的判定依据。
 * 注意：与后端 _display_state / _is_expired 保持一致——paused 优先于 expired，
 * 手动暂停的任务不视为过期（后端 _is_expired 在 paused 时返回 False）。 */
export function isTaskExpired(task: Task): boolean {
  if (!task.expires_at) return false;
  return new Date(task.expires_at).getTime() < Date.now();
}

/** 从 latest 聚合展示用状态（Home/App 共用）
 * 优先级与后端 _display_state 完全一致：
 *  - paused：手动暂停（优先于过期）
 *  - expired：任务过期
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
  // 与后端 _display_state 一致：paused 优先于 expired
  if (task.paused) state = 'paused';
  else if (isTaskExpired(task)) state = 'expired';
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
  /** 到期后自动切换的档位（后端 /quota 新增；F-N3：防御式渲染，不存在则忽略） */
  pending_tier?: Tier | string | null;
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

/** /notifications 历史记录：StockEvent 基础上带发送状态与失败原因；
 * kind 区分系统通知（task_auto_paused/quota_warning 等）与到货通知（stock_alert） */
export interface NotificationRecord extends StockEvent {
  kind?: string | null;
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

/**
 * 给无时区后缀的 ISO 时间补 Z（按 UTC 解析）。后端部分接口（如 /stats/poll）
 * 返回 UTC 裸字符串，不补的话浏览器会按本地时间误读（北京用户差 8 小时）。
 */
function withZ(iso: string | null): string | null {
  if (!iso) return iso;
  return /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`;
}

/* ---------------- 接口 ---------------- */

export const api = {
  register: (email: string, password: string) =>
    req<{
      id: number;
      email: string;
      tier: Tier;
      totp_enabled: boolean;
      /** R7：登录/注册时认领了匿名 device 任务会有提示文案（后端 auth.py _claim_notice） */
      notice?: string | null;
    }>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
  // 登录成功：后端返回 {ok:true, totp_required:bool, notice?} + Set-Cookie(session_token)
  login: (email: string, password: string) =>
    req<{ ok: boolean; totp_required: boolean; notice?: string | null }>('/auth/login', {
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
  // 后端 POST /tasks/{id}/renew 返回 TaskOut（完整任务），不是 {ok, expires_at}
  renewTask: (id: string | number) =>
    req<Task>(`/tasks/${id}/renew`, { method: 'POST' }),
  // POST /tasks/batch 直接支持 category/mode/channels；group/repeat_interval_sec 仍走 PATCH 补齐
  batchTasks: (payload: {
    part_numbers: string[];
    store_numbers: string[];
    name_template: string;
    category?: string;
    mode?: 'instant' | 'confirmed';
    channels?: TaskChannels;
  }) => req<Task[]>('/tasks/batch', { method: 'POST', body: JSON.stringify(payload) }),
  // F-1：单任务创建（POST /tasks），支持匿名（X-Device-Id 由 req 统一带）。
  // 匿名用户调这个逐个创建——后端 /tasks/batch 要求登录，匿名调 batch 会 401。
  createTask: (payload: {
    name: string;
    group?: string;
    category?: string;
    part_number: string;
    product_name?: string;
    color?: string;
    capacity?: string;
    stores: { number: string; name?: string; city?: string }[];
    mode?: 'instant' | 'confirmed';
    repeat_interval_sec?: number | null;
    channels?: TaskChannels;
  }) => req<Task>('/tasks', { method: 'POST', body: JSON.stringify(payload) }),
  updateTask: (id: string | number, payload: Partial<Task>) =>
    req<Task>(`/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteTask: (id: string | number) => req<void>(`/tasks/${id}`, { method: 'DELETE' }),

  taskStates: (id: string | number) => req<StateRow[]>(`/tasks/${id}/states`),

  // P0-5 双保险：refresh=1 时后端理论上返回纯数组，但若返回对象 {refreshing, stores}
  //（历史形状错位曾导致白屏），这里防御性解包，保证永远返回数组
  stores: async (refresh = 0): Promise<StoreRef[]> => {
    const data = await req<StoreRef[] | { refreshing?: boolean; stores?: StoreRef[] }>(
      `/catalog/stores?refresh=${refresh}`,
    );
    if (Array.isArray(data)) return data;
    return data?.stores ?? [];
  },
  products: (category: string) => req<Product[]>(`/catalog/products?category=${category}`),

  // R6-U4：后端测试失败时可能返回 {ok:false, error:'原因'}，error 优先展示
  notifyTest: (channel: string, target: string) =>
    req<{ ok: boolean; message?: string; error?: string }>('/notify/test', {
      method: 'POST',
      body: JSON.stringify({ channel, target }),
    }),
  notifications: (task_id?: string | number) =>
    req<NotificationRecord[]>(
      `/notifications${task_id ? `?task_id=${encodeURIComponent(String(task_id))}` : ''}`,
    ).then((list) =>
      // 后端字段名为 error，前端统一为 failure_reason
      list.map((n) => ({ ...n, failure_reason: n.failure_reason ?? (n as { error?: string | null }).error ?? null })),
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
  // F6：/stats/poll 的 last_poll_at 是 UTC 裸字符串（无 Z 后缀），前端解析时必须按 UTC 处理。
  // 后缀缺失时补 Z；若已有 Z 或 ±hh:mm 偏移则不动。再由各页转本地展示（页脚承诺"页面内所有时间为本地时间"）。
  pollStats: () =>
    req<PollStats>('/stats/poll').then((s) => ({
      ...s,
      last_poll_at: withZ(s.last_poll_at),
    })),

  quota: () => req<Quota>('/quota'),
  plans: () => req<Plan[]>('/plans'),
  payments: () => req<Payment[]>('/payments'),
};
