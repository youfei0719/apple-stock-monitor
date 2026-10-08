/**
 * 后台 API 客户端：对齐后端 snake_case 契约（backend/app/api/routers/*.py）。
 *
 * 后端是唯一真源；字段名一律 snake_case，不做 camelCase 转换。
 * 登录走 `POST /api/auth/login`（{email, password} → {ok, totp_required} + Set-Cookie），
 * 新管理员首次需走 `POST /api/auth/totp/setup` 绑 TOTP 再 verify。
 * 前端不存任何密钥 / token，敏感操作（改级）在页面层做二次确认。
 */
/** R9：mock 模式已下线（USE_MOCK 恒为 false），所有 mock 分支与 mock 数据已删除。
 * 该常量仅保留导出，供各页的 "(mock 数据)"类展示兜底判断使用。 */
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
  /** F3：后端 GET /api/admin/users 已返回 email_verified（R8 核对 admin.py:163，旧注释"暂未返回"已过期）；
   * 可选链保留，防御旧后端。没返回时按钮按"未验证"处理。 */
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

// ------------------------------ 真实请求 ------------------------------

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: 'include', // HttpOnly Cookie 会话，前端不碰 token
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  // R9-I6：401（会话页内过期）统一跳登录页——全站数据走原生 fetch，
  // main.tsx 的 react-query QueryCache 401 拦截是死逻辑（无 useQuery 在用）。
  // 守卫缓存一并清除，保证重新登录后守卫重新校验；已在登录页时不再跳转。
  if (res.status === 401 && !window.location.pathname.includes('/sign-in')) {
    try {
      // 动态 import 避免与 routes/_authenticated/route 的静态循环依赖
      const mod = await import('@/routes/_authenticated/route')
      mod.invalidateAdminGuardCache()
    } catch {
      /* 路由模块加载失败不影响跳转 */
    }
    window.location.href = '/admin/sign-in'
  }
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
  return req<LoginOut>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

/** 当前登录用户（判断 TOTP 是否已绑定） */
export async function getMe(): Promise<MeOut> {
  return req('/api/me')
}

/** TOTP 绑定：返回 secret + otpauth:// uri（前端本地渲染二维码，不经过第三方） */
export async function totpSetup(): Promise<TotpSetupOut> {
  return req('/api/auth/totp/setup', { method: 'POST' })
}

/** TOTP 验证码校验（setup 后或登录时） */
export async function totpVerify(code: string): Promise<{ ok: boolean; totp_enabled: boolean }> {
  return req('/api/auth/totp/verify', { method: 'POST', body: JSON.stringify({ code }) })
}

/** 总览 KPI */
export async function getOverview(): Promise<OverviewKpi> {
  return req('/api/admin/overview')
}

/** 流量：PV/UV 按日（后端字段 day） */
export async function getTraffic(days = 30): Promise<TrafficPoint[]> {
  return req(`/api/admin/traffic?days=${days}`)
}

/** 会员列表 */
export async function getUsers(q = '', tier: Tier | '' = ''): Promise<AdminUser[]> {
  const params = new URLSearchParams()
  if (q) params.set('q', q)
  if (tier) params.set('tier', tier)
  return req(`/api/admin/users?${params}`)
}

/** PATCH /api/admin/users/{id} 返回体。
 * R9-I12：后端改级默认 +30 天并在 notices 里提示（如"未传 tier_expires_at，已默认设为 +30 天"），
 * changes.tier_expires_at.to 是实际生效的到期时间——前端展示用它，不用自己传的 iso。 */
export interface UpdateUserTierOut {
  ok: boolean
  changes?: Record<string, { from?: string | null; to?: string | null } | number | string | boolean | null>
  notices?: string[]
}

/** 会员改级（写操作，页面层二次确认；后端记 audit）
 *  tier_expires_at：补单时手动设定到期时间（ISO），不传则后端保持原值
 */
export async function updateUserTier(
  id: number,
  tier: Tier,
  tier_expires_at?: string | null,
): Promise<UpdateUserTierOut> {
  const body: Record<string, unknown> = { tier }
  if (tier_expires_at !== undefined) body.tier_expires_at = tier_expires_at
  return req<UpdateUserTierOut>(`/api/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(body) })
}

/** F3：手动标记邮箱已验证——PATCH /api/admin/users/{id} {email_verified: true}
 *（SMTP 故障兜底；后端记审计。后端 list_users 已返回该字段（R9 核对 admin.py:163，
 * 旧注释"暂未返回"已过期）；可选链保留，防御旧后端。） */
export async function verifyUserEmail(id: number): Promise<void> {
  await req(`/api/admin/users/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ email_verified: true }),
  })
}

/** 付费记录；claimStatus='unclaimed' 时只看待认领坏账 */
export async function getPayments(
  claimStatus: '' | 'unclaimed' = '',
): Promise<PaymentRecord[]> {
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
  return req(`/api/admin/payments/${paymentId}/refund`, {
    method: 'POST',
    body: JSON.stringify({ force }),
  })
}

/** F2：关闭金额异常/待认领订单（不处理）——POST /api/admin/payments/{id}/close，
 * status 置 resolved（从待处理队列移除），不绑定用户、不开通档位（页面层二次确认） */
export async function closePayment(paymentId: number): Promise<PaymentActionOut> {
  return req(`/api/admin/payments/${paymentId}/close`, { method: 'POST' })
}

/** 认领坏账：把未认领订单绑定到用户（写操作，后端记 audit） */
export async function claimPayment(paymentId: number, userId: number): Promise<void> {
  await req(`/api/admin/payments/${paymentId}/claim`, {
    method: 'POST',
    body: JSON.stringify({ user_id: userId }),
  })
}

/** 系统状态：{engine, apple_cooldown, log_tail} */
export async function getSystem(): Promise<SystemStatus> {
  return req('/api/admin/system')
}

/** 高峰模式开关返回体（F7：后端实际返回 {ok, peak_mode}，不是 SystemStatus） */
export interface PeakModeOut {
  ok: boolean
  peak_mode: boolean
}

/** 高峰模式开关（写操作，后端记 audit；页面层二次确认） */
export async function setPeakMode(enabled: boolean): Promise<PeakModeOut> {
  return req<PeakModeOut>('/api/admin/system/peak-mode', {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  })
}

/** 审计日志 */
export async function getAudit(): Promise<AuditEntry[]> {
  return req('/api/admin/audit')
}

export const TIER_LABEL: Record<Tier, string> = {
  trial: '体验版',
  free: '免费版',
  standard: '标准会员',
  pro: 'Pro 会员',
}

/**
 * R9-I7：后台所有时间统一按北京时间（Asia/Shanghai）展示，与前台口径一致；
 * 后端返回 UTC ISO8601（带 Z），这里显式转北京时间。各页在表头/页注标注「北京时间」。
 */
export function fmtLocalTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  // 后端 naive datetime 是 UTC 裸串（SQLite 不存 tzinfo），无后缀时按 UTC 解析，
  // 否则浏览器按本地时间误读（与前台 api.ts withZ 同一口径）
  const normalized = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`
  const d = new Date(normalized)
  if (Number.isNaN(d.getTime())) return '—'
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23', // 午夜按 00 计（hour12:false 在部分实现是 h24，午夜会出 24:00）
  }).formatToParts(d)
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}`
}
