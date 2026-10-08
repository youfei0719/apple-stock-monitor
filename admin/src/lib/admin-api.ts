/**
 * 后台 API 客户端：对齐后端 snake_case 契约（backend/app/api/routers/*.py）。
 *
 * 后端是唯一真源；字段名一律 snake_case，不做 camelCase 转换。
 * 登录走 `POST /api/auth/login`（{email, password} → {ok, totp_required} + Set-Cookie），
 * 新管理员首次需走 `POST /api/auth/totp/setup` 绑 TOTP 再 verify。
 * 前端不存任何密钥 / token，敏感操作（改级）在页面层做二次确认。
 */
export const USE_MOCK = false

const API_BASE = import.meta.env.VITE_API_BASE ?? '' // 开发: http://localhost:8000；生产: 同源 ''

export type Tier = 'trial' | 'free' | 'standard' | 'pro' // 对齐后端 VALID_TIERS

export interface OverviewKpi {
  total_users: number
  tier_distribution: Record<string, number>
  today_pushes: number
  revenue_cny: number
  active_tasks: number
  /**
   * 待处理支付（后端 GET /api/admin/overview 已返回；unclaimed=未认领
   * （user_id 为空且 status∈{paid,amount_mismatch,unknown_plan}），
   * amount_mismatch=全局金额异常数）。R7：两者有重叠（未认领的金额异常订单
   * 同时计入两项），展示时只列分项、不加总。
   */
  pending_payments?: { unclaimed: number; amount_mismatch: number }
}

export interface TrafficPoint {
  day: string // 后端 GET /api/admin/traffic 字段名
  pv: number
  uv: number
}

export interface AdminUser {
  id: number
  email: string
  tier: Tier
  tier_expires_at: string | null
  is_admin: boolean
  totp_enabled: boolean
  /** F3：后端 PATCH /api/admin/users/{id} 已支持 email_verified，但 list_users 暂未返回该字段
   *（后端负责人补）。这里可选链防御：没返回时按钮按"未验证"处理。 */
  email_verified?: boolean | null
  created_at: string
}

export interface PaymentRecord {
  id: number
  user_id: number | null
  order_id: string
  plan: string
  amount_cny: number
  tier_from: string | null
  tier_to: string | null
  status: string // 后端实际值：'paid' / 'amount_mismatch' / 'refunded' / 'cancelled'
  /** 从 raw_payload 提取的爱发电备注（用户填写的用户 ID / 邮箱），用于认领坏账 */
  remark: string | null
  created_at: string
}

export interface EngineStatus {
  running: boolean
  last_heartbeat: string | null
  last_tick_at: string | null
  rounds_total: number
  rounds_ok: number
  last_error: string | null
}

export interface SystemStatus {
  engine: EngineStatus
  apple_cooldown: Record<string, unknown>
  log_tail: string[]
  /** 高峰模式：trial/free 间隔拉长、catalog 后台刷新暂停、pro 优先 */
  peak_mode: boolean
}

export interface AuditEntry {
  id: number
  admin_id: number
  action: string
  target_type: string
  target_id: string
  detail: unknown
  ip: string
  created_at: string
}

/** 后端登录返回体：{ok, totp_required} + Set-Cookie（无用户信息字段） */
export interface LoginOut {
  ok: boolean
  totp_required: boolean
}

/** GET /api/me（MeOut）：仅供登录后判断 TOTP 是否已绑定 */
export interface MeOut {
  id: number
  email: string
  tier: Tier
  quota: Record<string, unknown>
  totp_enabled: boolean
}

/** POST /api/auth/totp/setup 返回体 */
export interface TotpSetupOut {
  secret: string
  uri: string
  enabled: boolean
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

// ------------------------------ mock 数据（契约形状，snake_case） ------------------------------

function seededRand(seed: number) {
  let s = seed
  return () => {
    s = (s * 1103515245 + 12345) & 0x7fffffff
    return s / 0x7fffffff
  }
}

const MOCK_USERS: AdminUser[] = [
  { id: 1, email: 'youfei0719@gmail.com', tier: 'pro', tier_expires_at: '2026-11-08T10:02:11Z', is_admin: true, totp_enabled: true, created_at: '2026-10-02T03:12:00Z' },
  { id: 2, email: 'miffy.fan@163.com', tier: 'standard', tier_expires_at: '2026-11-08T11:44:02Z', is_admin: false, totp_enabled: false, created_at: '2026-10-03T09:40:00Z' },
  { id: 3, email: 'shenzhen.frank@outlook.com', tier: 'free', tier_expires_at: null, is_admin: false, totp_enabled: false, created_at: '2026-10-04T14:05:00Z' },
  { id: 4, email: 'kuromi_lover@qq.com', tier: 'trial', tier_expires_at: null, is_admin: false, totp_enabled: false, created_at: '2026-10-05T07:22:00Z' },
]

const MOCK_PAYMENTS: PaymentRecord[] = [
  { id: 1, user_id: 1, order_id: 'AFD20261008001', plan: 'afdian_plan_pro', amount_cny: 39, tier_from: 'free', tier_to: 'pro', status: 'paid', remark: '2', created_at: '2026-10-08T10:02:11Z' },
  { id: 2, user_id: 2, order_id: 'AFD20261008002', plan: 'afdian_plan_standard', amount_cny: 19, tier_from: 'free', tier_to: 'standard', status: 'paid', remark: 'miffy.fan@163.com', created_at: '2026-10-08T11:44:02Z' },
  { id: 3, user_id: null, order_id: 'AFD20261008003', plan: 'afdian_plan_standard', amount_cny: 19, tier_from: null, tier_to: null, status: 'paid', remark: null, created_at: '2026-10-08T13:20:00Z' },
  { id: 4, user_id: null, order_id: 'AFD20261008004', plan: 'afdian_plan_standard', amount_cny: 15, tier_from: null, tier_to: null, status: 'amount_mismatch', remark: 'shenzhen.frank@outlook.com', created_at: '2026-10-08T14:05:00Z' },
]

const MOCK_AUDIT: AuditEntry[] = [
  { id: 1, admin_id: 1, action: 'user.patch', target_type: 'user', target_id: '2', detail: { tier: { from: 'free', to: 'standard' } }, ip: '127.0.0.1', created_at: '2026-10-08T12:00:00Z' },
]

const MOCK_LOGS = [
  '2026-10-09T00:47:12Z [poll] store=R484 part=MJYC4CH/A → unavailable (412ms)',
  '2026-10-09T00:46:58Z [notify] bark → u-001: iPhone 18 Pro Max 银色 512GB 有货（R484）',
  '2026-10-09T00:46:40Z [engine] heartbeat ok',
]

function mockTraffic(days: number): TrafficPoint[] {
  const rand = seededRand(42)
  const out: TrafficPoint[] = []
  const now = new Date('2026-10-09T00:00:00Z')
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(now.getTime() - i * 86400000)
    const base = 800 + Math.sin(i / 4) * 200
    const pv = Math.round(base + rand() * 400)
    out.push({ day: d.toISOString().slice(0, 10), pv, uv: Math.round(pv * (0.35 + rand() * 0.15)) })
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

/**
 * 管理员登录第 1 步：邮箱 + 密码（后端 LoginIn 必需 email，只发 password 会 422）。
 * 返回后端登录体 {ok, totp_required} + Set-Cookie（无用户信息字段）。
 * 调用方按 totp_required 分流：第 2 步走 totpVerify（已绑定）或 /totp-setup（新管理员先绑定）。
 */
export async function adminLoginPassword(email: string, password: string): Promise<LoginOut> {
  if (USE_MOCK) {
    await sleep(600)
    return { ok: true, totp_required: true }
  }
  return req<LoginOut>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

/** 当前登录用户（判断 TOTP 是否已绑定） */
export async function getMe(): Promise<MeOut> {
  if (USE_MOCK) {
    await sleep(200)
    return { id: 1, email: 'youfei0719@gmail.com', tier: 'pro', quota: {}, totp_enabled: true }
  }
  return req('/api/me')
}

/** TOTP 绑定：返回 secret + otpauth:// uri（前端本地渲染二维码，不经过第三方） */
export async function totpSetup(): Promise<TotpSetupOut> {
  if (USE_MOCK) {
    await sleep(300)
    return {
      secret: 'JBSWY3DPEHPK3PXP',
      uri: 'otpauth://totp/admin?secret=JBSWY3DPEHPK3PXP&issuer=stockmon',
      enabled: false,
    }
  }
  return req('/api/auth/totp/setup', { method: 'POST' })
}

/** TOTP 验证码校验（setup 后或登录时） */
export async function totpVerify(code: string): Promise<{ ok: boolean; totp_enabled: boolean }> {
  if (USE_MOCK) {
    await sleep(400)
    if (code === '000000') throw new AdminApiError('验证码错误', 'bad_totp', 400)
    return { ok: true, totp_enabled: true }
  }
  return req('/api/auth/totp/verify', { method: 'POST', body: JSON.stringify({ code }) })
}

/** 总览 KPI */
export async function getOverview(): Promise<OverviewKpi> {
  if (USE_MOCK) {
    await sleep(300)
    return {
      total_users: 1284,
      tier_distribution: { trial: 96, free: 1000, standard: 142, pro: 46 },
      today_pushes: 2130,
      revenue_cny: 4827,
      active_tasks: 96,
    }
  }
  return req('/api/admin/overview')
}

/** 流量：PV/UV 按日（后端字段 day） */
export async function getTraffic(days = 30): Promise<TrafficPoint[]> {
  if (USE_MOCK) {
    await sleep(300)
    return mockTraffic(days)
  }
  return req(`/api/admin/traffic?days=${days}`)
}

/** 会员列表 */
export async function getUsers(q = '', tier: Tier | '' = ''): Promise<AdminUser[]> {
  if (USE_MOCK) {
    await sleep(300)
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

/** 会员改级（写操作，页面层二次确认；后端记 audit）
 *  tier_expires_at：补单时手动设定到期时间（ISO），不传则后端保持原值
 */
export async function updateUserTier(
  id: number,
  tier: Tier,
  tier_expires_at?: string | null,
): Promise<void> {
  if (USE_MOCK) {
    await sleep(400)
    const u = MOCK_USERS.find((x) => x.id === id)
    if (u) {
      MOCK_AUDIT.unshift({
        id: Date.now(),
        admin_id: 1,
        action: 'user.patch',
        target_type: 'user',
        target_id: String(id),
        detail: { tier: { from: u.tier, to: tier }, tier_expires_at },
        ip: '127.0.0.1',
        created_at: new Date().toISOString(),
      })
      u.tier = tier
      if (tier_expires_at !== undefined) u.tier_expires_at = tier_expires_at
    }
    return
  }
  const body: Record<string, unknown> = { tier }
  if (tier_expires_at !== undefined) body.tier_expires_at = tier_expires_at
  await req(`/api/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(body) })
}

/** F3：手动标记邮箱已验证——PATCH /api/admin/users/{id} {email_verified: true}
 *（SMTP 故障兜底；后端记审计。后端 list_users 暂未返回该字段，页面用可选链防御。） */
export async function verifyUserEmail(id: number): Promise<void> {
  if (USE_MOCK) {
    await sleep(400)
    const u = MOCK_USERS.find((x) => x.id === id)
    if (u) {
      u.email_verified = true
      MOCK_AUDIT.unshift({
        id: Date.now(),
        admin_id: 1,
        action: 'user.verify_email',
        target_type: 'user',
        target_id: String(id),
        detail: { email_verified: true },
        ip: '127.0.0.1',
        created_at: new Date().toISOString(),
      })
    }
    return
  }
  await req(`/api/admin/users/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ email_verified: true }),
  })
}

/** 付费记录；claimStatus='unclaimed' 时只看待认领坏账 */
export async function getPayments(
  claimStatus: '' | 'unclaimed' = '',
): Promise<PaymentRecord[]> {
  if (USE_MOCK) {
    await sleep(300)
    return claimStatus === 'unclaimed'
      ? MOCK_PAYMENTS.filter((p) => p.user_id === null)
      : MOCK_PAYMENTS
  }
  const params = new URLSearchParams()
  if (claimStatus) params.set('claim_status', claimStatus)
  const q = params.toString()
  return req(`/api/admin/payments${q ? `?${q}` : ''}`)
}

/** 认领坏账：把未认领订单绑定到用户（写操作，后端记 audit） */
export interface PaymentActionOut {
  ok: boolean
  payment_id: number
  payment_status: string
  pending_count?: number
  user?: { user_id: number; tier: { from: string; to: string } } | null
}

/**
 * F1：标记退款——POST /api/admin/payments/{id}/refund（后端联动降回 free、记审计；页面层二次确认）。
 * R7：force 参数——后端 R5-B-3：用户还有其他有效 paid 订单时默认 400
 * （code=has_active_paid_orders），管理员二次确认后带 force=true 重调才真正降档。
 */
export async function refundPayment(paymentId: number, force = false): Promise<PaymentActionOut> {
  if (USE_MOCK) {
    await sleep(400)
    const p = MOCK_PAYMENTS.find((x) => x.id === paymentId)
    if (p) {
      p.status = 'refunded'
      MOCK_AUDIT.unshift({
        id: Date.now(),
        admin_id: 1,
        action: 'payment.refund',
        target_type: 'payment',
        target_id: String(paymentId),
        detail: { from: 'paid', to: 'refunded', force },
        ip: '127.0.0.1',
        created_at: new Date().toISOString(),
      })
    }
    return { ok: true, payment_id: paymentId, payment_status: 'refunded', user: null }
  }
  return req(`/api/admin/payments/${paymentId}/refund`, {
    method: 'POST',
    body: JSON.stringify({ force }),
  })
}

/** F2：关闭金额异常/待认领订单（不处理）——POST /api/admin/payments/{id}/close，
 * status 置 resolved（从待处理队列移除），不绑定用户、不开通档位（页面层二次确认） */
export async function closePayment(paymentId: number): Promise<PaymentActionOut> {
  if (USE_MOCK) {
    await sleep(400)
    const p = MOCK_PAYMENTS.find((x) => x.id === paymentId)
    if (p) {
      p.status = 'resolved'
      MOCK_AUDIT.unshift({
        id: Date.now(),
        admin_id: 1,
        action: 'payment.close',
        target_type: 'payment',
        target_id: String(paymentId),
        detail: { from: 'amount_mismatch', to: 'resolved' },
        ip: '127.0.0.1',
        created_at: new Date().toISOString(),
      })
    }
    return { ok: true, payment_id: paymentId, payment_status: 'resolved' }
  }
  return req(`/api/admin/payments/${paymentId}/close`, { method: 'POST' })
}

/** 认领坏账：把未认领订单绑定到用户（写操作，后端记 audit） */
export async function claimPayment(paymentId: number, userId: number): Promise<void> {
  if (USE_MOCK) {
    await sleep(400)
    const p = MOCK_PAYMENTS.find((x) => x.id === paymentId)
    if (p) {
      p.user_id = userId
      MOCK_AUDIT.unshift({
        id: Date.now(),
        admin_id: 1,
        action: 'payment.claim',
        target_type: 'payment',
        target_id: String(paymentId),
        detail: { user_id: userId },
        ip: '127.0.0.1',
        created_at: new Date().toISOString(),
      })
    }
    return
  }
  await req(`/api/admin/payments/${paymentId}/claim`, {
    method: 'POST',
    body: JSON.stringify({ user_id: userId }),
  })
}

/** 系统状态：{engine, apple_cooldown, log_tail} */
export async function getSystem(): Promise<SystemStatus> {
  if (USE_MOCK) {
    await sleep(300)
    return {
      engine: {
        running: true,
        last_heartbeat: '2026-10-09T00:47:12Z',
        last_tick_at: '2026-10-09T00:47:12Z',
        rounds_total: 1024,
        rounds_ok: 1011,
        last_error: null,
      },
      apple_cooldown: { until: null, reason: '' },
      log_tail: MOCK_LOGS,
      peak_mode: false,
    }
  }
  return req('/api/admin/system')
}

/** 高峰模式开关返回体（F7：后端实际返回 {ok, peak_mode}，不是 SystemStatus） */
export interface PeakModeOut {
  ok: boolean
  peak_mode: boolean
}

/** 高峰模式开关（写操作，后端记 audit；页面层二次确认） */
export async function setPeakMode(enabled: boolean): Promise<PeakModeOut> {
  if (USE_MOCK) {
    await sleep(400)
    return { ok: true, peak_mode: enabled }
  }
  return req<PeakModeOut>('/api/admin/system/peak-mode', {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  })
}

/** 审计日志 */
export async function getAudit(): Promise<AuditEntry[]> {
  if (USE_MOCK) {
    await sleep(200)
    return MOCK_AUDIT
  }
  return req('/api/admin/audit')
}

/** 日志 tail：直接取 GET /api/admin/system 的 log_tail 字段 */
export async function getLogTail(): Promise<string[]> {
  if (USE_MOCK) {
    await sleep(200)
    return MOCK_LOGS
  }
  const s = await getSystem()
  return s.log_tail ?? []
}

export const TIER_LABEL: Record<Tier, string> = {
  trial: '体验版',
  free: '免费版',
  standard: '标准会员',
  pro: 'Pro 会员',
}

/**
 * P2 时区：后端所有时间都是 UTC ISO8601（带 Z）。后台统一转浏览器本地时间展示，
 * 与前台「页面内所有时间为本地时间」承诺一致；各页在表头/页注标注「本地时间」。
 */
export function fmtLocalTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
