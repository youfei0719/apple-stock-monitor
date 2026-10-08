import { useEffect, useState } from 'react'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { AdminProfile } from '@/components/admin-profile'
import { ThemeSwitch } from '@/components/theme-switch'
import { getTraffic, isAuthExpired, type TrafficPoint } from '@/lib/admin-api'

/** R8-U-7：后端缺天不返回，前端按 days 把缺的天补 0，避免折线断裂。
 * R9-U2：补零日期键必须用北京时间（后端 day 键是北京时间）——
 * 浏览器本地时区下，UTC±X 的管理员日期会错位一天。 */
function fillZeroDays(data: TrafficPoint[], days: number): TrafficPoint[] {
  const byDay = new Map(data.map((d) => [d.day, d]))
  const out: TrafficPoint[] = []
  // en-CA 产出 YYYY-MM-DD，与后端 day 键同形；Asia/Shanghai 显式指定北京时间
  const beijingDay = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
  const now = Date.now()
  for (let i = days - 1; i >= 0; i--) {
    // 北京时间无夏令时，按 24h 步进即连续自然日
    const key = beijingDay.format(new Date(now - i * 86400000))
    out.push(byDay.get(key) ?? { day: key, pv: 0, uv: 0 })
  }
  return out
}

export function Traffic() {
  const [days, setDays] = useState('30')
  const [data, setData] = useState<TrafficPoint[] | null>(null)
  // R8-I-13：加载失败单独记错误态，区分"加载失败"与"无数据"
  const [loadError, setLoadError] = useState<string | null>(null)

  const load = (d: string) => {
    setData(null)
    setLoadError(null)
    getTraffic(Number(d))
      .then((pts) => setData(fillZeroDays(pts, Number(d))))
      // R8-I-13：失败不伪装成空数组；R10-P2-8：会话过期已跳转登录页，静默吞掉
      .catch((e) => {
        if (isAuthExpired(e)) return
        setLoadError(e instanceof Error ? e.message : '加载流量数据失败')
      })
  }

  useEffect(() => {
    load(days)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days])

  const loading = data === null && loadError === null
  const totalPv = data?.reduce((s, d) => s + d.pv, 0) ?? null
  const totalUv = data?.reduce((s, d) => s + d.uv, 0) ?? null

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
            <h1 className='text-2xl font-bold tracking-tight'>流量</h1>
            <p className='text-sm text-muted-foreground'>
              访问流量趋势（PV / UV，按日）
            </p>
          </div>
          <Select value={days} onValueChange={setDays}>
            <SelectTrigger className='w-32 rounded-2xl'>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='7'>近 7 天</SelectItem>
              <SelectItem value='30'>近 30 天</SelectItem>
              <SelectItem value='90'>近 90 天</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className='grid gap-4 sm:grid-cols-2'>
          <Card className='rounded-3xl'>
            <CardHeader className='pb-2'>
              <CardTitle className='text-sm font-medium text-muted-foreground'>
                累计 PV
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className='font-mono text-3xl font-semibold'>
                {totalPv === null ? '—' : totalPv.toLocaleString()}
              </div>
            </CardContent>
          </Card>
          <Card className='rounded-3xl'>
            <CardHeader className='pb-2'>
              <CardTitle className='text-sm font-medium text-muted-foreground'>
                {/* R10-P2-7：按日去重 UV 直接加总不是区间去重 UV，改名"各日 UV 之和"避免误导 */}
                各日 UV 之和
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className='font-mono text-3xl font-semibold'>
                {totalUv === null ? '—' : totalUv.toLocaleString()}
              </div>
            </CardContent>
          </Card>
        </div>

        <Card className='mt-4 rounded-3xl'>
          <CardHeader>
            <CardTitle>PV / UV 趋势</CardTitle>
            <CardDescription>日粒度访问量</CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <Skeleton className='h-80 w-full rounded-2xl' />
            ) : data === null ? (
              <div className='py-12 text-center'>
                <p className='text-sm text-[#d70015]'>加载失败：{loadError ?? '未知错误'}</p>
                <button
                  onClick={() => load(days)}
                  className='mt-3 rounded-2xl bg-muted px-4 py-2 text-sm font-medium hover:text-foreground'
                >
                  重试
                </button>
              </div>
            ) : (
              <ResponsiveContainer width='100%' height={360}>
                <LineChart data={data} margin={{ left: 0, right: 8, top: 8 }}>
                  <CartesianGrid strokeDasharray='3 3' stroke='#e8e8ed' />
                  <XAxis
                    dataKey='day'
                    tick={{ fontSize: 11, fill: '#86868b' }}
                    // 后端 day 形如 2026-10-09，取 MM-DD 显示
                    tickFormatter={(v: string) => v.slice(5)}
                    minTickGap={24}
                  />
                  <YAxis tick={{ fontSize: 11, fill: '#86868b' }} />
                  <Tooltip
                    contentStyle={{
                      borderRadius: 16,
                      border: '1px solid #e8e8ed',
                      fontFamily: "'Paper Mono', ui-monospace, monospace",
                    }}
                  />
                  <Legend />
                  <Line
                    type='monotone'
                    dataKey='pv'
                    name='PV'
                    stroke='#0071e3'
                    strokeWidth={2.5}
                    dot={false}
                  />
                  <Line
                    type='monotone'
                    dataKey='uv'
                    name='UV'
                    stroke='#86868b'
                    strokeWidth={2}
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
      </Main>
    </>
  )
}
