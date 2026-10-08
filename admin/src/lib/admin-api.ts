/**
 * 后台 API 客户端（对齐 docs/API_CONTRACT.md 的 /api/admin/* 章节）
 *
 * ============================================================================
 * TODO(后端联调): 把 USE_MOCK 改为 false 即可切换到真实接口。
 *   - 真实模式下所有请求走同源 `/api/admin/*`，携带 HttpOnly Cookie 会话
 *   - 登录走 `POST /api/auth/login` + `POST /api/auth/totp/verify`（后端校验）
 *   - 会员改级接口契约待后端确认：当前假设 `PATCH /api/admin/users/{id}` {tier}
 *   - 前端不存任何密钥 / token，敏感操作（改级）在页面层做二次确认
 * ============================================================================
 */
export const USE_MOCK = false

const API_BASE = import.meta.env.VITE_API_BASE ?? '' // 开发: http://localhost:8000；生产: 同源 ''

export type Tier = 'free' | 'standard' | 'pro'

export interface OverviewKpi {
  totalUsers: number
  tierDist: Record<Tier, number>
  todayPushes: number
  revenueCny: number
  activeTasks: number
}

export interface TrafficPoint {
  date: string
  pv: number
  uv: number
}

export interface AdminUser {
  id: string
  email: string
  tier: Tier
  tasksUsed: number
  pushUsed: number
  createdAt: string
  lastActiveAt: string
}

export interface PaymentRecord {
  id: string
  afdianOrderNo: string
  email: string
  amountCny: number
  tier: Tier
  status: 'success' | 'pending' | 'refunded'
  paidAt: string
}

export interface SystemStatus {
  engine: 'running' | 'degraded' | 'stopped'
  version: string
  queue: { pending: number; processing: number; failed24h: number }
  rateLimit: { appleApiPerMin: string; loginLocks: number; catalogRefresh: string }
  lastPollAt: string
  avgPollMs: number
  successRate: number
}

export interface AuditEntry {
  id: string
  admin: string
  action: string
  target: string
  detail: string
  at: string
}

export class AdminApiError extends Error {
  code: string
  status: number
  constructor(message: string, code = 'UNKNOWN', status = 0) {
    super(message)
    this.code = code
    this.status = status
  }
}

// ------------------------------ mock 数据 ------------------------------

function seededRand(seed: number) {
  let s = seed
  return () => {
    s = (s * 1103515245 + 12345) & 0x7fffffff
    return s / 0x7fffffff
  }
}

const MOCK_USERS: AdminUser[] = [
  { id: 'u-001', email: 'youfei0719@gmail.com', tier: 'pro', tasksUsed: 18, pushUsed: 320, createdAt: '2026-10-02T03:12:00Z', lastActiveAt: '2026-10-09T00:20:00Z' },
  { id: 'u-002', email: 'miffy.fan@163.com', tier: 'standard', tasksUsed: 6, pushUsed: 96, createdAt: '2026-10-03T09:40:00Z', lastActiveAt: '2026-10-08T22:10:00Z' },
  { id: 'u-003', email: 'shenzhen.frank@outlook.com', tier: 'free', tasksUsed: 2, pushUsed: 11, createdAt: '2026-10-04T14:05:00Z', lastActiveAt: '2026-10-08T18:44:00Z' },
  { id: 'u-004', email: 'kuromi_lover@qq.com', tier: 'standard', tasksUsed: 9, pushUsed: 140, createdAt: '2026-10-05T07:22:00Z', lastActiveAt: '2026-10-07T11:02:00Z' },
  { id: 'u-005', email: 'baoan.iphone@qq.com', tier: 'free', tasksUsed: 1, pushUsed: 3, createdAt: '2026-10-06T16:58:00Z', lastActiveAt: '2026-10-06T17:30:00Z' },
  { id: 'u-006', email: 'nanshan.dev@gmail.com', tier: 'pro', tasksUsed: 24, pushUsed: 512, createdAt: '2026-10-06T20:11:00Z', lastActiveAt: '2026-10-09T00:01:00Z' },
  { id: 'u-007', email: 'watch.s8@163.com', tier: 'free', tasksUsed: 0, pushUsed: 0, createdAt: '2026-10-07T08:33:00Z', lastActiveAt: '2026-10-07T08:35:00Z' },
  { id: 'u-008', email: 'mac.studio@foxmail.com', tier: 'standard', tasksUsed: 7, pushUsed: 88, createdAt: '2026-10-08T02:47:00Z', lastActiveAt: '2026-10-08T21:19:00Z' },
]

const MOCK_PAYMENTS: PaymentRecord[] = [
  { id: 'p-001', afdianOrderNo: 'AFD20261008001', email: 'youfei0719@gmail.com', amountCny: 39, tier: 'pro', status: 'success', paidAt: '2026-10-08T10:02:11Z' },
  { id: 'p-002', afdianOrderNo: 'AFD20261008002', email: 'miffy.fan@163.com', amountCny: 19, tier: 'standard', status: 'success', paidAt: '2026-10-08T11:44:02Z' },
  { id: 'p-003', afdianOrderNo: 'AFD20261008003', email: 'kuromi_lover@qq.com', amountCny: 19, tier: 'standard', status: 'success', paidAt: '2026-10-08T15:20:37Z' },
  { id: 'p-004', afdianOrderNo: 'AFD20261008004', email: 'nanshan.dev@gmail.com', amountCny: 39, tier: 'pro', status: 'success', paidAt: '2026-10-08T19:08:55Z' },
  { id: 'p-005', afdianOrderNo: 'AFD20261008005', email: 'mac.studio@foxmail.com', amountCny: 19, tier: 'standard', status: 'pending', paidAt: '2026-10-09T00:12:40Z' },
  { id: 'p-006', afdianOrderNo: 'AFD20261007001', email: 'watch.s8@163.com', amountCny: 19, tier: 'standard', status: 'refunded', paidAt: '2026-10-07T09:31:00Z' },
]

const MOCK_AUDIT: AuditEntry[] = [
  { id: 'a-001', admin: 'admin', action: '改级', target: 'miffy.fan@163.com', detail: 'free → standard（手动）', at: '2026-10-08T12:00:00Z' },
  { id: 'a-002', admin: 'admin', action: '登录', target: '-', detail: 'TOTP 校验通过', at: '2026-10-09T00:30:00Z' },
]

const MOCK_LOGS = [
  '2026-10-09T00:47:12Z [poll] store=R484 part=MJYC4CH/A → unavailable (412ms)',
  '2026-10-09T00:47:09Z [poll] store=R761 part=MJYC4CH/A → unavailable (388ms)',
  '2026-10-09T00:47:05Z [poll] store=R793 part=MJYC4CH/A → unavailable (401ms)',
  '2026-10-09T00:46:58Z [notify] bark → u-001: iPhone 18 Pro Max 银色 512GB 有货（R484）',
  '2026-10-09T00:46:40Z [engine] heartbeat ok, workers=4',
]

function mockTraffic(days: number): TrafficPoint[] {
  const rand = seededRand(42)
  const out: TrafficPoint[] = []
  const now = new Date('2026-10-09T00:00:00Z')
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(now.getTime() - i * 86400000)
    const base = 800 + Math.sin(i / 4) * 200
    const pv = Math.round(base + rand() * 400)
    out.push({ date: d.toISOString().slice(0, 10), pv, uv: Math.round(pv * (0.35 + rand() * 0.15)) })
  }
  return out
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

// ------------------------------ 真实请求 ------------------------------

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: 'include', // HttpOnly Cookie 会话，前端不碰 token
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new AdminApiError(
      body.detail ?? `请求失败 (${res.status})`,
      body.code ?? 'HTTP_ERROR',
      res.status,
    )
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

// ------------------------------ 对外接口 ------------------------------

/** 管理员登录：密码 + TOTP（校验全在后端） */
export async function adminLogin(password: string, totp: string): Promise<void> {
  if (USE_MOCK) {
    await sleep(600)
    // mock 行为（便于验证失败/限流提示 UI，真实模式由后端校验）：
    // 验证码填 000000 时模拟"动态验证码错误" → 401
    if (totp === '000000') {
      throw new AdminApiError('动态验证码错误', 'INVALID_TOTP', 401)
    }
    return
  }
  await req('/api/auth/login', { method: 'POST', body: JSON.stringify({ password }) })
  await req('/api/auth/totp/verify', { method: 'POST', body: JSON.stringify({ code: totp }) })
}

/** 总览 KPI */
export async function getOverview(): Promise<OverviewKpi> {
  if (USE_MOCK) {
    await sleep(300)
    // TODO(后端联调): GET /api/admin/overview
    return {
      totalUsers: 1284,
      tierDist: { free: 1096, standard: 142, pro: 46 },
      todayPushes: 2130,
      revenueCny: 4827,
      activeTasks: 96,
    }
  }
  return req('/api/admin/overview')
}

/** 流量：PV/UV 按日 */
export async function getTraffic(days = 30): Promise<TrafficPoint[]> {
  if (USE_MOCK) {
    await sleep(300)
    // TODO(后端联调): GET /api/admin/traffic?days=30
    return mockTraffic(days)
  }
  return req(`/api/admin/traffic?days=${days}`)
}

/** 会员列表 */
export async function getUsers(q = '', tier: Tier | '' = ''): Promise<AdminUser[]> {
  if (USE_MOCK) {
    await sleep(300)
    // TODO(后端联调): GET /api/admin/users?q=&tier=
    return MOCK_USERS.filter(
      (u) =>
        (!q || u.email.toLowerCase().includes(q.toLowerCase())) &&
        (!tier || u.tier === tier),
    )
  }
  const params = new URLSearchParams()
  if (q) params.set('q', q)
  if (tier) params.set('tier', tier)
  return req(`/api/admin/users?${params}`)
}

/** 会员改级（写操作，页面层二次确认；后端记 audit） */
export async function updateUserTier(id: string, tier: Tier): Promise<void> {
  if (USE_MOCK) {
    await sleep(400)
    // TODO(后端联调): PATCH /api/admin/users/{id} {tier}（契约待后端确认）
    const u = MOCK_USERS.find((x) => x.id === id)
    if (u) {
      MOCK_AUDIT.unshift({
        id: `a-${Date.now()}`,
        admin: 'admin',
        action: '改级',
        target: u.email,
        detail: `${u.tier} → ${tier}（mock）`,
        at: new Date().toISOString(),
      })
      u.tier = tier
    }
    return
  }
  await req(`/api/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify({ tier }) })
}

/** 付费记录 */
export async function getPayments(): Promise<PaymentRecord[]> {
  if (USE_MOCK) {
    await sleep(300)
    // TODO(后端联调): GET /api/admin/payments
    return MOCK_PAYMENTS
  }
  return req('/api/admin/payments')
}

/** 系统状态 */
export async function getSystem(): Promise<SystemStatus> {
  if (USE_MOCK) {
    await sleep(300)
    // TODO(后端联调): GET /api/admin/system
    return {
      engine: 'running',
      version: 'v1.0.0-mock',
      queue: { pending: 12, processing: 4, failed24h: 1 },
      rateLimit: { appleApiPerMin: '47 / 60', loginLocks: 0, catalogRefresh: '42 分钟前' },
      lastPollAt: '2026-10-09T00:47:12Z',
      avgPollMs: 402,
      successRate: 0.987,
    }
  }
  return req('/api/admin/system')
}

/** 审计日志 */
export async function getAudit(): Promise<AuditEntry[]> {
  if (USE_MOCK) {
    await sleep(200)
    // TODO(后端联调): GET /api/admin/audit
    return MOCK_AUDIT
  }
  return req('/api/admin/audit')
}

/** 日志 tail（mock） */
export async function getLogTail(): Promise<string[]> {
  if (USE_MOCK) {
    await sleep(200)
    // TODO(后端联调): 并入 GET /api/admin/system 返回
    return MOCK_LOGS
  }
  const s = await req<SystemStatus & { logs: string[] }>('/api/admin/system')
  return s.logs ?? []
}

export const TIER_LABEL: Record<Tier, string> = {
  free: '免费版',
  standard: '标准会员',
  pro: 'Pro 会员',
}
