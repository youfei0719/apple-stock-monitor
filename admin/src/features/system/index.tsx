import { useEffect, useState } from 'react'
import { Activity, Inbox, Gauge, ShieldCheck } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { AdminProfile } from '@/components/admin-profile'
import { ThemeSwitch } from '@/components/theme-switch'
import {
  getSystem,
  getAudit,
  getLogTail,
  type SystemStatus,
  type AuditEntry,
  USE_MOCK,
} from '@/lib/admin-api'

function StatCard({
  title,
  value,
  icon: Icon,
  loading,
}: {
  title: string
  value: string
  icon: React.ElementType
  loading: boolean
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
          <Skeleton className='h-8 w-24 rounded-xl' />
        ) : (
          <div className='font-mono text-2xl font-semibold'>{value}</div>
        )}
      </CardContent>
    </Card>
  )
}

export function System() {
  const [sys, setSys] = useState<SystemStatus | null>(null)
  const [logs, setLogs] = useState<string[] | null>(null)
  const [audit, setAudit] = useState<AuditEntry[] | null>(null)

  useEffect(() => {
    getSystem().then(setSys).catch(() => setSys(null))
    getLogTail().then(setLogs).catch(() => setLogs([]))
    getAudit().then(setAudit).catch(() => setAudit([]))
  }, [])

  const loading = sys === null
  const engineOk = sys?.engine === 'running'

  return (
    <>
      <Header>
        <div className='me-auto text-sm font-medium'>库存监控后台</div>
        <ThemeSwitch />
        <AdminProfile />
      </Header>
      <Main>
        <div className='mb-6'>
          <h1 className='text-2xl font-bold tracking-tight'>系统状态</h1>
          <p className='text-sm text-muted-foreground'>
            引擎 / 队列 / 限流 / 日志 / 管理员审计
            {USE_MOCK && '（mock 数据，待后端联调）'}
          </p>
        </div>

        <div className='grid gap-4 sm:grid-cols-2 lg:grid-cols-4'>
          <Card className='rounded-3xl'>
            <CardHeader className='flex flex-row items-center justify-between pb-2'>
              <CardTitle className='text-sm font-medium text-muted-foreground'>
                引擎状态
              </CardTitle>
              <span className='flex h-9 w-9 items-center justify-center rounded-2xl bg-[#0071e3]/10 text-[#0071e3]'>
                <Activity className='h-4.5 w-4.5' />
              </span>
            </CardHeader>
            <CardContent>
              {loading ? (
                <Skeleton className='h-8 w-24 rounded-xl' />
              ) : (
                <Badge
                  className={`rounded-full text-sm ${engineOk ? 'bg-[#34c759]/10 text-[#1f8a3d]' : 'bg-[#ff9f0a]/10 text-[#b26a00]'}`}
                >
                  ● {engineOk ? '运行中' : sys?.engine}
                </Badge>
              )}
              <p className='mt-2 font-mono text-xs text-muted-foreground'>
                {sys?.version ?? '—'}
              </p>
            </CardContent>
          </Card>
          <StatCard
            title='队列待处理'
            value={sys ? `${sys.queue.pending}` : '—'}
            icon={Inbox}
            loading={loading}
          />
          <StatCard
            title='平均轮询耗时'
            value={sys ? `${sys.avgPollMs}ms` : '—'}
            icon={Gauge}
            loading={loading}
          />
          <StatCard
            title='轮询成功率'
            value={sys ? `${(sys.successRate * 100).toFixed(1)}%` : '—'}
            icon={ShieldCheck}
            loading={loading}
          />
        </div>

        <div className='mt-4 grid gap-4 lg:grid-cols-2'>
          <Card className='rounded-3xl'>
            <CardHeader>
              <CardTitle>限流状态</CardTitle>
              <CardDescription>各关键路径的当前配额使用</CardDescription>
            </CardHeader>
            <CardContent>
              {loading ? (
                <Skeleton className='h-32 w-full rounded-2xl' />
              ) : (
                <dl className='space-y-3 text-sm'>
                  <div className='flex justify-between rounded-2xl bg-muted px-4 py-3'>
                    <dt className='text-muted-foreground'>Apple 查询接口</dt>
                    <dd className='font-mono font-semibold'>
                      {sys!.rateLimit.appleApiPerMin} / 分钟
                    </dd>
                  </div>
                  <div className='flex justify-between rounded-2xl bg-muted px-4 py-3'>
                    <dt className='text-muted-foreground'>登录锁定 IP</dt>
                    <dd className='font-mono font-semibold'>
                      {sys!.rateLimit.loginLocks} 个
                    </dd>
                  </div>
                  <div className='flex justify-between rounded-2xl bg-muted px-4 py-3'>
                    <dt className='text-muted-foreground'>目录上次刷新</dt>
                    <dd className='font-mono font-semibold'>
                      {sys!.rateLimit.catalogRefresh}
                    </dd>
                  </div>
                  <div className='flex justify-between rounded-2xl bg-muted px-4 py-3'>
                    <dt className='text-muted-foreground'>24h 失败任务</dt>
                    <dd className='font-mono font-semibold'>
                      {sys!.queue.failed24h}
                    </dd>
                  </div>
                </dl>
              )}
            </CardContent>
          </Card>

          <Card className='rounded-3xl'>
            <CardHeader>
              <CardTitle>日志 tail</CardTitle>
              <CardDescription>
                最近引擎日志（上次轮询 {sys?.lastPollAt.slice(11, 19) ?? '—'} UTC）
              </CardDescription>
            </CardHeader>
            <CardContent>
              {logs === null ? (
                <Skeleton className='h-40 w-full rounded-2xl' />
              ) : (
                <pre className='max-h-56 overflow-auto rounded-2xl bg-[#1d1d1f] p-4 font-mono text-[12px] leading-relaxed text-[#f5f5f7]'>
                  {logs.map((l, i) => (
                    <div key={i}>{l}</div>
                  ))}
                </pre>
              )}
            </CardContent>
          </Card>
        </div>

        <Card className='mt-4 rounded-3xl'>
          <CardHeader>
            <CardTitle>管理员操作审计</CardTitle>
            <CardDescription>所有写操作均记审计（后端落库）</CardDescription>
          </CardHeader>
          <CardContent>
            {audit === null ? (
              <Skeleton className='h-40 w-full rounded-2xl' />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>时间</TableHead>
                    <TableHead>管理员</TableHead>
                    <TableHead>操作</TableHead>
                    <TableHead>对象</TableHead>
                    <TableHead>详情</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {audit.map((a) => (
                    <TableRow key={a.id}>
                      <TableCell className='font-mono text-[13px] text-muted-foreground'>
                        {a.at.slice(0, 16).replace('T', ' ')}
                      </TableCell>
                      <TableCell className='font-medium'>{a.admin}</TableCell>
                      <TableCell>
                        <Badge className='rounded-full bg-[#0071e3]/10 text-[#0071e3]'>
                          {a.action}
                        </Badge>
                      </TableCell>
                      <TableCell>{a.target}</TableCell>
                      <TableCell className='text-muted-foreground'>{a.detail}</TableCell>
                    </TableRow>
                  ))}
                  {audit.length === 0 && (
                    <TableRow>
                      <TableCell
                        colSpan={5}
                        className='py-12 text-center text-muted-foreground'
                      >
                        暂无审计记录
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </Main>
    </>
  )
}
