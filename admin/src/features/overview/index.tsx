import { useEffect, useState } from 'react'
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
  type OverviewKpi,
  type Tier,
  TIER_LABEL,
  USE_MOCK,
} from '@/lib/admin-api'

const TIER_COLORS: Record<Tier, string> = {
  free: 'bg-[#86868b]',
  standard: 'bg-[#0071e3]',
  pro: 'bg-[#af52de]',
}

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

  useEffect(() => {
    getOverview().then(setData).catch(() => setData(null))
  }, [])

  const loading = data === null
  const total =
    data == null ? 0 : data.tierDist.free + data.tierDist.standard + data.tierDist.pro

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
              今日运营关键指标 {USE_MOCK && '（mock 数据，待后端联调）'}
            </p>
          </div>
        </div>

        <div className='grid gap-4 sm:grid-cols-2 lg:grid-cols-4'>
          <KpiCard
            title='注册用户'
            value={data ? data.totalUsers.toLocaleString() : '—'}
            sub={`监控任务进行中 ${data?.activeTasks ?? '—'} 个`}
            icon={Users}
            loading={loading}
          />
          <KpiCard
            title='付费会员'
            value={
              data
                ? (data.tierDist.standard + data.tierDist.pro).toLocaleString()
                : '—'
            }
            sub='标准 + Pro 合计'
            icon={Activity}
            loading={loading}
          />
          <KpiCard
            title='今日推送'
            value={data ? data.todayPushes.toLocaleString() : '—'}
            sub='到货通知发送量'
            icon={Send}
            loading={loading}
          />
          <KpiCard
            title='累计收入'
            value={data ? `¥${data.revenueCny.toLocaleString()}` : '—'}
            sub='爱发电到账（税后口径）'
            icon={Wallet}
            loading={loading}
          />
        </div>

        <Card className='mt-4 rounded-3xl'>
          <CardHeader>
            <CardTitle>会员分布</CardTitle>
            <CardDescription>按 tier 划分的用户构成</CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <Skeleton className='h-24 w-full rounded-2xl' />
            ) : (
              <div className='space-y-4'>
                <div className='flex h-4 w-full overflow-hidden rounded-full bg-muted'>
                  {(Object.keys(data!.tierDist) as Tier[]).map((t) => (
                    <div
                      key={t}
                      className={TIER_COLORS[t]}
                      style={{ width: `${(data!.tierDist[t] / total) * 100}%` }}
                      title={`${TIER_LABEL[t]}: ${data!.tierDist[t]}`}
                    />
                  ))}
                </div>
                <div className='grid grid-cols-3 gap-4'>
                  {(Object.keys(data!.tierDist) as Tier[]).map((t) => (
                    <div key={t} className='flex items-center gap-3'>
                      <span
                        className={`h-3 w-3 rounded-full ${TIER_COLORS[t]}`}
                      />
                      <div>
                        <div className='text-sm font-medium'>{TIER_LABEL[t]}</div>
                        <div className='font-mono text-lg font-semibold'>
                          {data!.tierDist[t].toLocaleString()}
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
