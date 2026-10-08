import { useEffect, useState } from 'react'
import { Activity, Inbox, Gauge, ShieldCheck, Zap } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
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
  setPeakMode,
  fmtLocalTime,
  type SystemStatus,
  type AuditEntry,
  USE_MOCK,
} from '@/lib/admin-api'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'

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



function fmtDetail(detail: unknown): string {
  if (detail == null) return '—'
  if (typeof detail === 'string') return detail
  try {
    return JSON.stringify(detail)
  } catch {
    return String(detail)
  }
}

/** R8-U-5：审计"操作"列中文映射（未知 action 回退显示原文） */
const AUDIT_ACTION_LABEL: Record<string, string> = {
  'user.patch': '会员改级',
  'user.verify_email': '邮箱手动验证',
  'payment.claim': '订单认领',
  'payment.refund': '订单退款',
  'payment.close': '订单关闭',
  'system.peak_mode': '高峰模式切换',
}

export function System() {
  const [sys, setSys] = useState<SystemStatus | null>(null)
  const [audit, setAudit] = useState<AuditEntry[] | null>(null)
  // R8-I-13：加载失败单独记错误态，区分"加载失败"与"无数据"/骨架屏
  const [sysError, setSysError] = useState<string | null>(null)
  const [auditError, setAuditError] = useState<string | null>(null)
  const [peakBusy, setPeakBusy] = useState(false)
  // P2：高峰模式开关二次确认
  const [peakPending, setPeakPending] = useState<boolean | null>(null)

  // R8-U-4：日志直接取 sys.log_tail，不再单独调 getLogTail（原先 /api/admin/system 被请求两次）
  const loadAll = () => {
    setSysError(null)
    getSystem()
      .then((s) => setSys(s))
      .catch((e) => setSysError(e instanceof Error ? e.message : '加载系统状态失败'))
    setAuditError(null)
    getAudit()
      .then((a) => setAudit(a))
      .catch((e) => setAuditError(e instanceof Error ? e.message : '加载审计日志失败'))
  }

  useEffect(() => {
    loadAll()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function togglePeakMode(enabled: boolean) {
    setPeakBusy(true)
    try {
      const s = await setPeakMode(enabled)
      // F7：setPeakMode 实际返回 {ok, peak_mode}（不是 SystemStatus）
      setSys((prev) => (prev ? { ...prev, peak_mode: s.peak_mode } : prev))
      toast.success(enabled ? '已开启高峰模式' : '已关闭高峰模式')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '切换失败')
    } finally {
      setPeakBusy(false)
      setPeakPending(null)
    }
  }

  const loading = sys === null && sysError === null
  // R8-U-4：日志直接取 sys.log_tail（getSystem 一次请求已包含）
  const logs = sys?.log_tail ?? null
  // 后端 engine 是 dict：{running, last_heartbeat, last_tick_at, rounds_total, rounds_ok, last_error}
  const engine = sys?.engine
  const engineOk = engine?.running === true
  const okRate =
    engine && engine.rounds_total > 0
      ? `${((engine.rounds_ok / engine.rounds_total) * 100).toFixed(1)}%`
      : '—'
  const cooldownEntries = sys ? Object.entries(sys.apple_cooldown ?? {}) : []

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
            引擎 / Apple 冷却 / 日志 / 管理员审计 · 时间均为北京时间
            {USE_MOCK && '（mock 数据，待后端联调）'}
          </p>
        </div>

        {/* R8-I-13：加载失败明确展示 + 重试，不再无限骨架屏 */}
        {sysError && (
          <Card className='mb-4 rounded-3xl border-[#d70015]/30'>
            <CardContent className='flex items-center justify-between px-5 py-4'>
              <p className='text-sm text-[#d70015]'>系统状态加载失败：{sysError}</p>
              <button
                onClick={loadAll}
                className='rounded-2xl bg-muted px-4 py-2 text-sm font-medium hover:text-foreground'
              >
                重试
              </button>
            </CardContent>
          </Card>
        )}

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
                  ● {engineOk ? '运行中' : '已停止'}
                </Badge>
              )}
              <p className='mt-2 font-mono text-xs text-muted-foreground'>
                上次心跳 {fmtLocalTime(engine?.last_heartbeat)}（北京时间）
              </p>
              {engine?.last_error && (
                <p className='mt-1 font-mono text-xs text-[#d70015]'>
                  错误：{engine.last_error}
                </p>
              )}
            </CardContent>
          </Card>
          <StatCard
            title='轮询总轮次'
            value={engine ? `${engine.rounds_total}` : '—'}
            icon={Inbox}
            loading={loading}
          />
          <StatCard
            title='轮询成功轮次'
            value={engine ? `${engine.rounds_ok}` : '—'}
            icon={Gauge}
            loading={loading}
          />
          <StatCard
            title='轮次成功率'
            value={okRate}
            icon={ShieldCheck}
            loading={loading}
          />
        </div>

        {/* 高峰模式开关（对标 DING果：新品期 trial/free 降速、pro 优先） */}
        <Card className='mt-4 rounded-3xl border-[#ff9f0a]/30'>
          <CardHeader className='flex flex-row items-center justify-between'>
            <div>
              <CardTitle className='flex items-center gap-2'>
                <Zap className='h-4.5 w-4.5 text-[#ff9f0a]' />
                高峰模式
                {sys?.peak_mode && (
                  <Badge className='rounded-full bg-[#ff9f0a]/10 text-[#b26a00]'>
                    ● 已开启
                  </Badge>
                )}
              </CardTitle>
              <CardDescription className='mt-1'>
                开启后：trial / free 轮询间隔拉长、catalog 后台刷新暂停、pro 任务优先。
                适用于新品发售等全站流量高峰。
              </CardDescription>
            </div>
            <Switch
              checked={sys?.peak_mode === true}
              disabled={loading || peakBusy}
              // P2：高峰模式影响全站轮询策略，开关前二次确认
              onCheckedChange={(v) => setPeakPending(v)}
              aria-label='高峰模式开关'
            />
          </CardHeader>
        </Card>

        {/* P2：高峰模式二次确认 */}
        <AlertDialog open={peakPending !== null} onOpenChange={(o) => !o && setPeakPending(null)}>
          <AlertDialogContent className='rounded-3xl'>
            <AlertDialogHeader>
              <AlertDialogTitle>
                确认{peakPending ? '开启' : '关闭'}高峰模式？
              </AlertDialogTitle>
              <AlertDialogDescription>
                {peakPending ? (
                  <>
                    开启后：trial / free 轮询间隔拉长、catalog 后台刷新暂停、pro 任务优先。
                    适用于新品发售等全站流量高峰。
                  </>
                ) : (
                  <>关闭后各档位恢复正常轮询间隔。</>
                )}
                该操作会写入管理员审计日志。
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel className='rounded-2xl'>取消</AlertDialogCancel>
              <AlertDialogAction
                className='rounded-2xl bg-[#ff9f0a] hover:bg-[#ff9f0a]/90 text-white'
                onClick={() => peakPending !== null && togglePeakMode(peakPending)}
                disabled={peakBusy}
              >
                {peakBusy ? '处理中…' : `确认${peakPending ? '开启' : '关闭'}`}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

        <div className='mt-4 grid gap-4 lg:grid-cols-2'>
          <Card className='rounded-3xl'>
            <CardHeader>
              <CardTitle>Apple 接口冷却</CardTitle>
              <CardDescription>后端 apple_cooldown 配置快照</CardDescription>
            </CardHeader>
            <CardContent>
              {loading ? (
                <Skeleton className='h-32 w-full rounded-2xl' />
              ) : cooldownEntries.length === 0 ? (
                <p className='py-6 text-center text-sm text-muted-foreground'>
                  当前无冷却配置
                </p>
              ) : (
                <dl className='space-y-3 text-sm'>
                  {cooldownEntries.map(([k, v]) => (
                    <div
                      key={k}
                      className='flex justify-between gap-4 rounded-2xl bg-muted px-4 py-3'
                    >
                      <dt className='font-mono text-muted-foreground'>{k}</dt>
                      <dd className='font-mono font-semibold break-all'>
                        {typeof v === 'string' ? v : JSON.stringify(v)}
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
            </CardContent>
          </Card>

          <Card className='rounded-3xl'>
            <CardHeader>
              <CardTitle>日志 tail</CardTitle>
              <CardDescription>
                后端 GET /api/admin/system 的 log_tail（最近引擎日志）
              </CardDescription>
            </CardHeader>
            <CardContent>
              {logs === null ? (
                sysError ? (
                  <p className='py-6 text-center text-sm text-[#d70015]'>
                    日志加载失败：{sysError}
                  </p>
                ) : (
                  <Skeleton className='h-40 w-full rounded-2xl' />
                )
              ) : logs.length === 0 ? (
                <p className='py-6 text-center text-sm text-muted-foreground'>
                  暂无日志
                </p>
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
              auditError ? (
                <p className='py-6 text-center text-sm text-[#d70015]'>
                  审计日志加载失败：{auditError}
                  <button
                    onClick={loadAll}
                    className='mt-3 block rounded-2xl bg-muted px-4 py-2 text-sm font-medium text-foreground hover:text-foreground'
                  >
                    重试
                  </button>
                </p>
              ) : (
                <Skeleton className='h-40 w-full rounded-2xl' />
              )
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>时间（北京时间）</TableHead>
                    <TableHead className='font-mono'>管理员 ID</TableHead>
                    <TableHead>操作</TableHead>
                    <TableHead>对象</TableHead>
                    <TableHead>详情</TableHead>
                    <TableHead className='font-mono'>IP</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {audit.map((a) => (
                    <TableRow key={a.id}>
                      <TableCell className='font-mono text-[13px] text-muted-foreground'>
                        {fmtLocalTime(a.created_at)}
                      </TableCell>
                      <TableCell className='font-mono text-[13px]'>{a.admin_id}</TableCell>
                      <TableCell>
                        <Badge className='rounded-full bg-[#0071e3]/10 text-[#0071e3]'>
                          {AUDIT_ACTION_LABEL[a.action] ?? a.action}
                        </Badge>
                      </TableCell>
                      <TableCell className='font-mono text-[13px]'>
                        {a.target_type}:{a.target_id}
                      </TableCell>
                      <TableCell className='max-w-64 truncate text-muted-foreground' title={fmtDetail(a.detail)}>
                        {fmtDetail(a.detail)}
                      </TableCell>
                      <TableCell className='font-mono text-[13px] text-muted-foreground'>
                        {a.ip}
                      </TableCell>
                    </TableRow>
                  ))}
                  {audit.length === 0 && (
                    <TableRow>
                      <TableCell
                        colSpan={6}
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
