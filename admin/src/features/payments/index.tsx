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
  AdminApiError,
  claimPayment,
  closePayment,
  fmtLocalTime,
  getPayments,
  refundPayment,
  type PaymentActionOut,
  type PaymentRecord,
  type Tier,
  TIER_LABEL,
  isAuthExpired,
} from '@/lib/admin-api'

const STATUS_LABEL: Record<string, string> = {
  paid: '已到账',
  amount_mismatch: '金额异常',
  refunded: '已退款',
  resolved: '已处理',
  cancelled: '已取消',
  unknown_plan: '未知档位',
}

/** 可标记退款的状态（后端仅拦截已退款；cancelled/resolved 为终态不重复操作） */
function canRefund(r: PaymentRecord): boolean {
  return r.status === 'paid' || r.status === 'amount_mismatch' || r.status === 'unknown_plan'
}
/** 可关闭（不处理）的状态：后端 close_payment 仅允许 amount_mismatch/unknown_plan → resolved；
 * paid（已开通）必须走退款流程（400 use_refund_flow），前端不给 paid 渲染"关闭"入口 */
function canClose(r: PaymentRecord): boolean {
  return ['amount_mismatch', 'unknown_plan'].includes(r.status)
}
/** R7：已处理完的终态订单（已退款/已处理/已取消）不再给认领入口 */
const CLAIM_HIDDEN_STATUSES = new Set(['refunded', 'resolved', 'cancelled'])

function fmtTierChange(r: PaymentRecord): string {
  const to = r.tier_to ? (TIER_LABEL[r.tier_to as Tier] ?? r.tier_to) : '—'
  if (!r.tier_from) return to
  const from = TIER_LABEL[r.tier_from as Tier] ?? r.tier_from
  return `${from} → ${to}`
}

/** R8-U-3：套餐列中文映射（tier_to 优先识别 standard/pro；unknown 或空 → '—'） */
function fmtPlan(r: PaymentRecord): string {
  if (r.tier_to === 'standard') return '标准版'
  if (r.tier_to === 'pro') return 'Pro版'
  const p = (r.plan ?? '').toLowerCase()
  if (p.includes('standard')) return '标准版'
  if (p.includes('pro')) return 'Pro版'
  return '—'
}

export function Payments() {
  const [rows, setRows] = useState<PaymentRecord[] | null>(null)
  // R8-I-14：全量订单（仅"只看待认领"时请求），收入合计按全站算、不随筛选变化
  const [fullRows, setFullRows] = useState<PaymentRecord[] | null>(null)
  // R8-I-13：加载失败单独记错误态，明确区分"加载失败"与"无数据"
  const [loadError, setLoadError] = useState<string | null>(null)
  const [unclaimedOnly, setUnclaimedOnly] = useState(false)
  const [claiming, setClaiming] = useState<PaymentRecord | null>(null)
  const [claimUserId, setClaimUserId] = useState('')
  const [claimBusy, setClaimBusy] = useState(false)
  // F1/F2：退款 / 关闭（不处理）二次确认
  const [refunding, setRefunding] = useState<PaymentRecord | null>(null)
  const [closing, setClosing] = useState<PaymentRecord | null>(null)
  const [actionBusy, setActionBusy] = useState(false)
  // R9-U1：force 退款二次确认（has_active_paid_orders）走应用内 AlertDialog，不用 window.confirm
  const [forceConfirm, setForceConfirm] = useState(false)
  // R10-P2-1：force 弹窗展示用的订单快照（弹 force 弹窗时退款弹窗已关闭，refunding 为 null）
  const [forceOrder, setForceOrder] = useState<PaymentRecord | null>(null)

  const load = async (unclaimed: boolean) => {
    setRows(null)
    setFullRows(null)
    setLoadError(null)
    try {
      if (unclaimed) {
        // R8-I-14：切"只看待认领"时 rows 是子集，收入合计仍按全量算（多一次请求）
        const [sub, all] = await Promise.all([
          getPayments('unclaimed'),
          getPayments(''),
        ])
        setRows(sub)
        setFullRows(all)
      } else {
        setRows(await getPayments(''))
      }
    } catch (e) {
      // R8-I-13：失败不伪装成空数组，"暂无数据"只在真正无数据时出现；
      // R10-P2-8：会话过期已跳转登录页，静默吞掉
      if (isAuthExpired(e)) return
      setLoadError(e instanceof Error ? e.message : '加载付费记录失败')
    }
  }

  useEffect(() => {
    load(unclaimedOnly)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unclaimedOnly])

  // R8-I-14："成功到账合计"按全站全量订单算，切"只看待认领"时不再跟着变
  const totalSource = unclaimedOnly ? fullRows : rows
  const total =
    totalSource?.reduce((s, r) => s + (r.status === 'paid' ? r.amount_cny : 0), 0) ?? null

  async function confirmClaim() {
    if (!claiming) return
    const userId = parseInt(claimUserId, 10)
    if (!Number.isFinite(userId) || userId <= 0) {
      toast.error('请输入有效的用户 ID（数字）')
      return
    }
    setClaimBusy(true)
    try {
      const out = await claimPayment(claiming.id, userId)
      // R10-I1：后端认领返回 notices（如"自动恢复 N 个因档位超限被暂停的监控任务"），
      // toast 一并展示，不再只报固定文案
      const notices = (out.notices ?? []).length > 0 ? `（${(out.notices ?? []).join('；')}）` : ''
      toast.success(`已将订单 ${claiming.order_id} 认领给用户 #${userId}${notices}`)
      setClaiming(null)
      setClaimUserId('')
      await load(unclaimedOnly)
    } catch (e) {
      if (isAuthExpired(e)) return
      toast.error(e instanceof Error ? e.message : '认领失败')
    } finally {
      setClaimBusy(false)
    }
  }

  /** F1：标记退款——后端联动把用户降回 free、revenue 只计 paid 自动排除、记审计 */
  async function confirmRefund(force = false) {
    // R10-P2-1：force 路径走 forceOrder 快照（退款弹窗已关闭，refunding 为 null）
    const target = force ? forceOrder : refunding
    if (!target) return
    setActionBusy(true)
    try {
      let out: PaymentActionOut
      try {
        out = await refundPayment(target.id, force)
      } catch (e) {
        // R7：后端 R5-B-3——该用户还有其他有效 paid 订单时 400 has_active_paid_orders；
        // R9-U1：force 二次确认改用应用内 AlertDialog（R8-U-8 规范），不用 window.confirm
        if (!force && e instanceof AdminApiError && e.code === 'has_active_paid_orders') {
          // R10-P2-1：弹 force 弹窗前先关掉退款弹窗，避免两个 AlertDialog 同时 open 叠加；
          // 订单信息另存 forceOrder 快照（refunding 已置 null）
          setForceOrder(target)
          setRefunding(null)
          setForceConfirm(true)
          return
        }
        throw e
      }
      toast.success(
        `订单 ${target.order_id} 已标记退款${out.user ? `（用户 #${out.user.user_id} 降回免费版）` : ''}`,
      )
      setRefunding(null)
      setForceOrder(null)
      await load(unclaimedOnly)
    } catch (e) {
      if (isAuthExpired(e)) return
      toast.error(e instanceof Error ? e.message : '标记退款失败')
    } finally {
      setActionBusy(false)
    }
  }

  /** F2：关闭（不处理）——status 置 resolved，从待处理队列移除，不绑定用户、不开通档位 */
  async function confirmClose() {
    if (!closing) return
    setActionBusy(true)
    try {
      await closePayment(closing.id)
      toast.success(`订单 ${closing.order_id} 已关闭（不处理）`)
      setClosing(null)
      await load(unclaimedOnly)
    } catch (e) {
      if (isAuthExpired(e)) return
      toast.error(e instanceof Error ? e.message : '关闭订单失败')
    } finally {
      setActionBusy(false)
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
            </p>
          </div>
          <Card className='rounded-3xl px-5 py-3'>
            <div className='text-xs text-muted-foreground'>成功到账合计（全站）</div>
            <div className='font-mono text-2xl font-semibold'>
              {total === null ? '—' : `¥${total.toLocaleString()}`}
            </div>
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
              loadError ? (
                <div className='py-12 text-center'>
                  <p className='text-sm text-[#d70015]'>加载失败：{loadError}</p>
                  <button
                    onClick={() => load(unclaimedOnly)}
                    className='mt-3 rounded-2xl bg-muted px-4 py-2 text-sm font-medium hover:text-foreground'
                  >
                    重试
                  </button>
                </div>
              ) : (
                <Skeleton className='h-64 w-full rounded-2xl' />
              )
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
                    <TableHead>支付时间（北京时间）</TableHead>
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
                        <TableCell className='text-[13px]'>{fmtPlan(r)}</TableCell>
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
                          {fmtLocalTime(r.created_at)}
                        </TableCell>
                        <TableCell className='text-right'>
                          <div className='flex justify-end gap-1.5'>
                            {/* R7：已处理完的终态订单（已退款/已处理/已取消）不渲染认领入口；
                                R8-I-10：后端 claim_payment 要求 tier_to ∈ (standard, pro)，否则 400 bad_tier，
                                unknown_plan 订单 tier_to 非法也不给认领入口 */}
                            {r.user_id === null &&
                              !CLAIM_HIDDEN_STATUSES.has(r.status) &&
                              r.tier_to !== null &&
                              ['standard', 'pro'].includes(r.tier_to) && (
                              <button
                                onClick={() => {
                                  setClaiming(r)
                                  setClaimUserId('')
                                }}
                                className='rounded-2xl bg-[#0071e3] px-3.5 py-1.5 text-[13px] font-medium text-white hover:bg-[#0071e3]/90'
                              >
                                认领
                              </button>
                            )}
                            {canRefund(r) && (
                              <button
                                onClick={() => setRefunding(r)}
                                className='rounded-2xl bg-[#ff9f0a]/10 px-3.5 py-1.5 text-[13px] font-medium text-[#b26a00] hover:bg-[#ff9f0a]/20'
                                title='标记退款：联动把用户降回免费版，后端记审计'
                              >
                                标记退款
                              </button>
                            )}
                            {canClose(r) && (
                              <button
                                onClick={() => setClosing(r)}
                                className='rounded-2xl bg-muted px-3.5 py-1.5 text-[13px] font-medium text-muted-foreground hover:text-foreground'
                                title='关闭（不处理）：状态置"已处理"，从待处理队列移除，不开通档位'
                              >
                                关闭（不处理）
                              </button>
                            )}
                            {r.user_id !== null && !canRefund(r) && !canClose(r) && (
                              <span className='text-muted-foreground'>—</span>
                            )}
                          </div>
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

      {/* F1：标记退款二次确认 */}
      <AlertDialog open={refunding !== null} onOpenChange={(o) => !o && setRefunding(null)}>
        <AlertDialogContent className='rounded-3xl'>
          <AlertDialogHeader>
            <AlertDialogTitle>确认标记退款？</AlertDialogTitle>
            <AlertDialogDescription>
              将订单 <span className='font-mono text-foreground'>{refunding?.order_id}</span>
              （¥{refunding?.amount_cny}，{refunding && (STATUS_LABEL[refunding.status] ?? refunding.status)}）
              标记为退款。该操作会：
              <br />
              1. 把订单状态置为「已退款」（收入统计只计已到账，自动排除）；
              <br />
              2. 把绑定的用户降回免费版并清空到期时间；
              <br />
              3. 写入管理员审计日志。退款本身需在爱发电后台操作。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className='rounded-2xl'>取消</AlertDialogCancel>
            <AlertDialogAction
              className='rounded-2xl bg-[#ff9f0a] hover:bg-[#ff9f0a]/90 text-white'
              onClick={() => void confirmRefund()}
              disabled={actionBusy}
            >
              {actionBusy ? '处理中…' : '确认标记退款'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* R9-U1：force 退款二次确认（has_active_paid_orders）——应用内弹窗，替代 window.confirm；
          R10-P2-1：与退款弹窗互斥（open 时退款弹窗已关闭），订单信息走 forceOrder 快照 */}
      <AlertDialog open={forceConfirm} onOpenChange={setForceConfirm}>
        <AlertDialogContent className='rounded-3xl'>
          <AlertDialogHeader>
            <AlertDialogTitle>仍要强制退款降级？</AlertDialogTitle>
            <AlertDialogDescription>
              订单 <span className='font-mono text-foreground'>{forceOrder?.order_id}</span>
              的用户还有其他有效付费订单。强制退款会把该用户直接降回免费版
              （其他有效订单不受影响）。该操作会写入管理员审计日志。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className='rounded-2xl'>取消</AlertDialogCancel>
            <AlertDialogAction
              className='rounded-2xl bg-[#d70015] hover:bg-[#d70015]/90 text-white'
              onClick={() => {
                setForceConfirm(false)
                void confirmRefund(true)
              }}
              disabled={actionBusy}
            >
              确认强制退款
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* F2：关闭（不处理）二次确认 */}
      <AlertDialog open={closing !== null} onOpenChange={(o) => !o && setClosing(null)}>
        <AlertDialogContent className='rounded-3xl'>
          <AlertDialogHeader>
            <AlertDialogTitle>确认关闭该订单？</AlertDialogTitle>
            <AlertDialogDescription>
              将订单 <span className='font-mono text-foreground'>{closing?.order_id}</span>
              （¥{closing?.amount_cny}，{closing && (STATUS_LABEL[closing.status] ?? closing.status)}）
              关闭（不处理）。状态将置为「已处理」，从待处理队列移除，不绑定用户、不开通任何档位。
              该操作会写入管理员审计日志。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className='rounded-2xl'>取消</AlertDialogCancel>
            <AlertDialogAction
              className='rounded-2xl bg-[#1d1d1f] hover:bg-[#1d1d1f]/90 text-white'
              onClick={confirmClose}
              disabled={actionBusy}
            >
              {actionBusy ? '处理中…' : '确认关闭'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
