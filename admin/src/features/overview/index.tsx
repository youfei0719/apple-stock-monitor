import { useEffect, useState } from 'react'
import { Link } from '@tanstack/react-router'
import { Users, Send, Wallet, Activity } from 'lucide-react'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { AdminProfile } from '@/components/admin-profile'
import { ThemeSwitch } from '@/components/theme-switch'
import {
  getOverview,
  isAuthExpired,
  type OverviewKpi,
  type Tier,
  TIER_LABEL,
} from '@/lib/admin-api'

const TIER_COLORS: Record<Tier, string> = {
  trial: 'bg-[#ff9f0a]',
  free: 'bg-[#86868b]',
  standard: 'bg-[#0071e3]',
  pro: 'bg-[#af52de]',
}
const TIER_COLOR_FALLBACK = 'bg-[#c7c7cc]'
const NON_PAID_TIERS = new Set(['trial', 'free'])

function KpiCard({
  title,
  value,
  sub,
  icon: Icon,
  loading,
  mono = true,
}: {
  title: string
  value: string
  sub: string
  icon: React.ElementType
  loading: boolean
  mono?: boolean
}) {
  return (
    <Card className='rounded-3xl'>
      <CardHeader className='flex flex-row items-center justify-between pb-2'>
        <CardTitle className='text-sm font-medium text-muted-foreground'>
          {title}
        </CardTitle>
        <span className='flex h-9 w-9 items-center justify-center rounded-2xl bg-[#0071e3]/10 text-[#0071e3]'>
          <Icon className='h-4.5 w-4.5' />
        </span>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className='h-9 w-28 rounded-xl' />
        ) : (
          <div
            className={`text-3xl font-semibold tracking-tight ${mono ? 'font-mono' : ''}`}
          >
            {value}
          </div>
        )}
        <p className='mt-1 text-xs text-muted-foreground'>{sub}</p>
      </CardContent>
    </Card>
  )
}

export function Overview() {
  const [data, setData] = useState<OverviewKpi | null>(null)
  // R8-I-13：加载失败单独记错误态，区分"加载失败"与骨架屏/无数据
  const [loadError, setLoadError] = useState<string | null>(null)

  const load = () => {
    setLoadError(null)
    getOverview()
      .then((d) => setData(d))
      .catch((e) => {
        // R10-P2-8：会话过期已跳转登录页，静默吞掉
        if (isAuthExpired(e)) return
        setLoadError(e instanceof Error ? e.message : '加载总览失败')
      })
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loading = data === null && loadError === null
  // 后端 tier_distribution 是 {tier: count} 对象，键来自 DB（trial/free/standard/pro）
  const dist = data?.tier_distribution ?? {}
  const total = Object.values(dist).reduce((s, c) => s + c, 0)
  const paid = Object.entries(dist)
    .filter(([t]) => !NON_PAID_TIERS.has(t))
    .reduce((s, [, c]) => s + c, 0)
  // 待处理支付 KPI（P2）：后端 /api/admin/overview 返回；可选链防御旧后端
  // R7 口径说明（对齐后端 admin.py overview）：unclaimed = user_id 为空 且
  // status ∈ {paid, amount_mismatch, unknown_plan} 的订单数；
  // amount_mismatch = 全局 status='amount_mismatch' 的订单数（不限 user_id，
  // 含已认领但尚未处理的）。两者有重叠（未认领的金额异常订单同时计入两项），
  // 故前端只展示分项、不加总——"待处理支付 X 笔"会把重叠部分重复计算。
  const pendingUnclaimed = data?.pending_payments?.unclaimed ?? 0
  const pendingMismatch = data?.pending_payments?.amount_mismatch ?? 0
  const hasPending = pendingUnclaimed > 0 || pendingMismatch > 0

  return (
    <>
      <Header>
        <div className='me-auto text-sm font-medium'>库存监控后台</div>
        <ThemeSwitch />
        <AdminProfile />
      </Header>
      <Main>
        <div className='mb-6 flex items-center justify-between'>
          <div>
            <h1 className='text-2xl font-bold tracking-tight'>总览</h1>
            <p className='text-sm text-muted-foreground'>
              今日运营关键指标
            </p>
          </div>
        </div>

        {/* R8-I-13：加载失败明确展示 + 重试，不再无限骨架屏 */}
        {loadError && (
          <Card className='mb-4 rounded-3xl border-[#d70015]/30'>
            <CardContent className='flex items-center justify-between px-5 py-4'>
              <p className='text-sm text-[#d70015]'>总览加载失败：{loadError}</p>
              <button
                onClick={load}
                className='rounded-2xl bg-muted px-4 py-2 text-sm font-medium hover:text-foreground'
              >
                重试
              </button>
            </CardContent>
          </Card>
        )}

        <div className='grid gap-4 sm:grid-cols-2 lg:grid-cols-4'>
          <KpiCard
            title='注册用户'
            value={data ? data.total_users.toLocaleString() : '—'}
            sub={`监控任务进行中 ${data?.active_tasks ?? '—'} 个`}
            icon={Users}
            loading={loading}
          />
          <KpiCard
            title='付费会员'
            value={data ? paid.toLocaleString() : '—'}
            // R10-I6：tier_distribution 是 DB 原始 tier 口径——到期后要等 sweep 才降为 free，
            // 窗口期内已过期用户仍被计入（与全库 effective_tier 口径矛盾）。
            // 后端暂无按有效档位统计的接口（admin.py /overview 未返回），真修法需后端加接口；
            // 当前先诚实标注"含已到期未降档"，避免把过期用户当付费会员数
            sub='标准 + Pro 合计（不含体验/免费；含已到期未降档）'
            icon={Activity}
            loading={loading}
          />
          <KpiCard
            title='今日推送'
            value={data ? data.today_pushes.toLocaleString() : '—'}
            sub='到货通知发送量'
            icon={Send}
            loading={loading}
          />
          <KpiCard
            title='累计收入'
            value={data ? `¥${data.revenue_cny.toLocaleString()}` : '—'}
            sub='爱发电到账（仅计 paid 状态，不含退款/异常）'
            icon={Wallet}
            loading={loading}
          />
        </div>

        {/* 待处理支付 KPI（P2）：unclaimed=待认领坏账，amount_mismatch=金额异常；为 0 时不打扰。
            R7：两项有重叠（见上方口径注释），只展示分项不加总 */}
        {!loading && hasPending && (
          <Card className='mt-4 rounded-3xl border-[#ff9f0a]/30'>
            <CardContent className='flex items-center justify-between px-5 py-4'>
              <div className='flex items-center gap-3'>
                <span className='flex h-9 w-9 items-center justify-center rounded-2xl bg-[#ff9f0a]/10 text-[#b26a00]'>
                  <Wallet className='h-4.5 w-4.5' />
                </span>
                <div>
                  <p className='text-sm font-medium'>待处理支付</p>
                  <p className='text-xs text-muted-foreground'>
                    待认领 {pendingUnclaimed} · 金额异常 {pendingMismatch}
                  </p>
                </div>
              </div>
              <Link
                to='/payments'
                className='rounded-2xl bg-[#ff9f0a]/10 px-4 py-2 text-sm font-medium text-[#b26a00] hover:bg-[#ff9f0a]/20'
              >
                去处理 →
              </Link>
            </CardContent>
          </Card>
        )}

        <Card className='mt-4 rounded-3xl'>
          <CardHeader>
            <CardTitle>会员分布</CardTitle>
            {/* R10-I6：同付费会员 KPI——DB 原始 tier 口径，含已到期未降档 */}
            <CardDescription>按 tier 划分的用户构成（含已到期未降档）</CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <Skeleton className='h-24 w-full rounded-2xl' />
            ) : (
              <div className='space-y-4'>
                <div className='flex h-4 w-full overflow-hidden rounded-full bg-muted'>
                  {Object.keys(dist).map((t) => (
                    <div
                      key={t}
                      className={TIER_COLORS[t as Tier] ?? TIER_COLOR_FALLBACK}
                      style={{ width: `${total ? (dist[t] / total) * 100 : 0}%` }}
                      title={`${TIER_LABEL[t as Tier] ?? t}: ${dist[t]}`}
                    />
                  ))}
                </div>
                <div className='grid grid-cols-2 gap-4 sm:grid-cols-4'>
                  {Object.keys(dist).map((t) => (
                    <div key={t} className='flex items-center gap-3'>
                      <span
                        className={`h-3 w-3 rounded-full ${TIER_COLORS[t as Tier] ?? TIER_COLOR_FALLBACK}`}
                      />
                      <div>
                        <div className='text-sm font-medium'>{TIER_LABEL[t as Tier] ?? t}</div>
                        <div className='font-mono text-lg font-semibold'>
                          {dist[t].toLocaleString()}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </Main>
    </>
  )
}
