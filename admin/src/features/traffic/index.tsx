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
import { getTraffic, type TrafficPoint, USE_MOCK } from '@/lib/admin-api'

export function Traffic() {
  const [days, setDays] = useState('30')
  const [data, setData] = useState<TrafficPoint[] | null>(null)

  useEffect(() => {
    setData(null)
    getTraffic(Number(days)).then(setData).catch(() => setData([]))
  }, [days])

  const loading = data === null
  const totalPv = data?.reduce((s, d) => s + d.pv, 0) ?? 0
  const totalUv = data?.reduce((s, d) => s + d.uv, 0) ?? 0

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
              {USE_MOCK && '（mock 数据，待后端联调）'}
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
                {loading ? '—' : totalPv.toLocaleString()}
              </div>
            </CardContent>
          </Card>
          <Card className='rounded-3xl'>
            <CardHeader className='pb-2'>
              <CardTitle className='text-sm font-medium text-muted-foreground'>
                累计 UV
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className='font-mono text-3xl font-semibold'>
                {loading ? '—' : totalUv.toLocaleString()}
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
