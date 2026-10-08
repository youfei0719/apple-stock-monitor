import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
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
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { AdminProfile } from '@/components/admin-profile'
import { ThemeSwitch } from '@/components/theme-switch'
import {
  claimPayment,
  getPayments,
  type PaymentRecord,
  type Tier,
  TIER_LABEL,
  USE_MOCK,
} from '@/lib/admin-api'

const STATUS_LABEL: Record<string, string> = {
  paid: '已到账',
  amount_mismatch: '金额异常',
  refunded: '已退款',
  cancelled: '已取消',
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  return iso.slice(0, 16).replace('T', ' ')
}

function fmtTierChange(r: PaymentRecord): string {
  const to = r.tier_to ? (TIER_LABEL[r.tier_to as Tier] ?? r.tier_to) : '—'
  if (!r.tier_from) return to
  const from = TIER_LABEL[r.tier_from as Tier] ?? r.tier_from
  return `${from} → ${to}`
}

export function Payments() {
  const [rows, setRows] = useState<PaymentRecord[] | null>(null)
  const [unclaimedOnly, setUnclaimedOnly] = useState(false)
  const [claiming, setClaiming] = useState<PaymentRecord | null>(null)
  const [claimUserId, setClaimUserId] = useState('')
  const [claimBusy, setClaimBusy] = useState(false)

  const load = async (unclaimed: boolean) => {
    setRows(null)
    try {
      setRows(await getPayments(unclaimed ? 'unclaimed' : ''))
    } catch {
      setRows([])
    }
  }

  useEffect(() => {
    load(unclaimedOnly)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unclaimedOnly])

  const total = rows?.reduce((s, r) => s + (r.status === 'paid' ? r.amount_cny : 0), 0) ?? 0

  async function confirmClaim() {
    if (!claiming) return
    const userId = parseInt(claimUserId, 10)
    if (!Number.isFinite(userId) || userId <= 0) {
      toast.error('请输入有效的用户 ID（数字）')
      return
    }
    setClaimBusy(true)
    try {
      await claimPayment(claiming.id, userId)
      toast.success(`已将订单 ${claiming.order_id} 认领给用户 #${userId}`)
      setClaiming(null)
      setClaimUserId('')
      await load(unclaimedOnly)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '认领失败')
    } finally {
      setClaimBusy(false)
    }
  }

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
            <h1 className='text-2xl font-bold tracking-tight'>付费</h1>
            <p className='text-sm text-muted-foreground'>
              爱发电付费记录（webhook 自动开通）
              {USE_MOCK && '（mock 数据，待后端联调）'}
            </p>
          </div>
          <Card className='rounded-3xl px-5 py-3'>
            <div className='text-xs text-muted-foreground'>成功到账合计</div>
            <div className='font-mono text-2xl font-semibold'>¥{total.toLocaleString()}</div>
          </Card>
        </div>

        <Card className='rounded-3xl'>
          <CardHeader>
            <div className='flex items-center justify-between'>
              <div>
                <CardTitle>付费记录</CardTitle>
                <CardDescription>按支付时间倒序</CardDescription>
              </div>
              <button
                onClick={() => setUnclaimedOnly((v) => !v)}
                className={`rounded-2xl px-4 py-2 text-sm font-medium transition ${
                  unclaimedOnly
                    ? 'bg-[#ff9f0a] text-white'
                    : 'bg-muted text-muted-foreground hover:text-foreground'
                }`}
              >
                {unclaimedOnly ? '● 只看待认领' : '只看待认领'}
              </button>
            </div>
          </CardHeader>
          <CardContent>
            {rows === null ? (
              <Skeleton className='h-64 w-full rounded-2xl' />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>爱发电订单号</TableHead>
                    <TableHead className='font-mono'>用户 ID</TableHead>
                    <TableHead>备注（认领依据）</TableHead>
                    <TableHead className='font-mono'>金额</TableHead>
                    <TableHead>套餐</TableHead>
                    <TableHead>等级变化</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>支付时间</TableHead>
                    <TableHead className='text-right'>操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((r) => {
                    const mismatch = r.status === 'amount_mismatch'
                    return (
                      <TableRow
                        key={r.id}
                        className={mismatch ? 'bg-[#d70015]/5' : undefined}
                      >
                        <TableCell className='font-mono text-[13px]'>
                          {r.order_id}
                        </TableCell>
                        <TableCell className='font-mono text-[13px] text-muted-foreground'>
                          {r.user_id ?? '—'}
                        </TableCell>
                        <TableCell className='max-w-48 truncate font-mono text-[13px]' title={r.remark ?? ''}>
                          {r.remark ?? <span className='text-muted-foreground'>—</span>}
                        </TableCell>
                        <TableCell
                          className={`font-mono font-semibold ${mismatch ? 'text-[#d70015]' : ''}`}
                        >
                          ¥{r.amount_cny}
                        </TableCell>
                        <TableCell className='font-mono text-[13px] text-muted-foreground'>
                          {r.plan}
                        </TableCell>
                        <TableCell>{fmtTierChange(r)}</TableCell>
                        <TableCell>
                          <Badge
                            className={`rounded-full ${
                              r.status === 'paid'
                                ? 'bg-[#34c759]/10 text-[#1f8a3d]'
                                : mismatch
                                  ? 'bg-[#d70015]/10 text-[#d70015]'
                                  : 'bg-[#e8e8ed] text-[#6e6e73]'
                            }`}
                          >
                            {STATUS_LABEL[r.status] ?? r.status}
                          </Badge>
                        </TableCell>
                        <TableCell className='text-muted-foreground'>
                          {fmtTime(r.created_at)}
                        </TableCell>
                        <TableCell className='text-right'>
                          {r.user_id === null ? (
                            <button
                              onClick={() => {
                                setClaiming(r)
                                setClaimUserId('')
                              }}
                              className='rounded-2xl bg-[#0071e3] px-3.5 py-1.5 text-[13px] font-medium text-white hover:bg-[#0071e3]/90'
                            >
                              认领
                            </button>
                          ) : (
                            <span className='text-muted-foreground'>—</span>
                          )}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                  {rows.length === 0 && (
                    <TableRow>
                      <TableCell
                        colSpan={9}
                        className='py-12 text-center text-muted-foreground'
                      >
                        {unclaimedOnly ? '没有待认领的订单 🎉' : '暂无付费记录'}
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </Main>

      {/* 认领：输入 user_id 绑定坏账订单 */}
      <AlertDialog open={claiming !== null} onOpenChange={(o) => !o && setClaiming(null)}>
        <AlertDialogContent className='rounded-3xl'>
          <AlertDialogHeader>
            <AlertDialogTitle>认领订单</AlertDialogTitle>
            <AlertDialogDescription>
              将订单 <span className='font-mono text-foreground'>{claiming?.order_id}</span>
              （¥{claiming?.amount_cny}
              {claiming?.remark ? `，备注「${claiming.remark}」` : ''}）
              绑定到指定用户。该操作会写入管理员审计日志。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <Input
            value={claimUserId}
            onChange={(e) => setClaimUserId(e.target.value.replace(/\D/g, ''))}
            inputMode='numeric'
            placeholder='用户 ID（数字）'
            className='rounded-2xl font-mono'
          />
          <AlertDialogFooter>
            <AlertDialogCancel className='rounded-2xl'>取消</AlertDialogCancel>
            <AlertDialogAction
              className='rounded-2xl bg-[#0071e3] hover:bg-[#0071e3]/90'
              onClick={confirmClaim}
              disabled={claimBusy}
            >
              {claimBusy ? '处理中…' : '确认认领'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
