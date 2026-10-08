import { useEffect, useMemo, useRef, useState } from 'react'
import { Search } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
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
import { Skeleton } from '@/components/ui/skeleton'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { AdminProfile } from '@/components/admin-profile'
import { ThemeSwitch } from '@/components/theme-switch'
import {
  fmtLocalTime,
  getUsers,
  updateUserTier,
  verifyUserEmail,
  isAuthExpired,
  type AdminUser,
  type Tier,
  TIER_LABEL,
} from '@/lib/admin-api'

/** R11-P1-1：会员列表每页条数（后端 limit≤200，真分页走 offset） */
const MEMBERS_PAGE_SIZE = 50

const TIER_BADGE: Record<Tier, string> = {
  trial: 'bg-[#ff9f0a]/10 text-[#b26a00]',
  free: 'bg-[#e8e8ed] text-[#1d1d1f]',
  standard: 'bg-[#0071e3]/10 text-[#0071e3]',
  pro: 'bg-[#af52de]/10 text-[#af52de]',
}

const TIERS: Tier[] = ['trial', 'free', 'standard', 'pro']

interface PendingChange {
  user: AdminUser
  tier: Tier
}

/** datetime-local 值 → ISO（无时区按本地时间理解，后端按 UTC 存） */
function toISO(local: string): string | null {
  if (!local) return null
  const d = new Date(local)
  return Number.isNaN(d.getTime()) ? null : d.toISOString()
}

/** ISO → datetime-local 输入值（yyyy-MM-ddTHH:mm） */
function toLocalInput(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}



export function Members() {
  const [users, setUsers] = useState<AdminUser[] | null>(null)
  const [q, setQ] = useState('')
  // R8-U-6：搜索输入先防抖，300ms 后再请求
  const [debouncedQ, setDebouncedQ] = useState('')
  // R8-I-13：加载失败单独记错误态，区分"加载失败"与"无数据"
  const [loadError, setLoadError] = useState<string | null>(null)
  const [tier, setTier] = useState<'all' | Tier>('all')
  const [pending, setPending] = useState<PendingChange | null>(null)
  const [changing, setChanging] = useState(false)
  const [expiresAt, setExpiresAt] = useState('')
  // F3：手动标记邮箱已验证（SMTP 故障兜底）
  const [verifying, setVerifying] = useState<AdminUser | null>(null)
  const [verifyBusy, setVerifyBusy] = useState(false)

  async function confirmVerifyEmail() {
    if (!verifying) return
    setVerifyBusy(true)
    try {
      await verifyUserEmail(verifying.id)
      setUsers((prev) =>
        prev?.map((u) =>
          u.id === verifying.id ? { ...u, email_verified: true } : u,
        ) ?? null,
      )
      toast.success(`已将 ${verifying.email} 标记为邮箱已验证（已记审计）`)
    } catch (e) {
      if (isAuthExpired(e)) return
      toast.error(e instanceof Error ? e.message : '标记失败')
    } finally {
      setVerifyBusy(false)
      setVerifying(null)
    }
  }

  // R8-U-6：搜索输入 300ms 防抖，避免每击键一次请求
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300)
    return () => clearTimeout(t)
  }, [q])

  const load = (query: string, t: 'all' | Tier, p: number) => {
    setUsers(null)
    setLoadError(null)
    // R11-P1-1：后端 /api/admin/users 支持 offset 真分页（R10 后端已补）；
    // 按 offset=p*PAGE_SIZE 拉取，响应为裸 list
    getUsers(query, t === 'all' ? '' : t, MEMBERS_PAGE_SIZE, p * MEMBERS_PAGE_SIZE)
      .then((u) => setUsers(u))
      // R8-I-13：失败不伪装成空数组，"没有符合条件的会员"只在真正无数据时出现；
      // R10-P2-8：会话过期已跳转登录页，静默吞掉
      .catch((e) => {
        if (isAuthExpired(e)) return
        setLoadError(e instanceof Error ? e.message : '加载会员列表失败')
      })
  }

  const [page, setPage] = useState(0)
  // R11-P1-1：搜索/档位变化 → 回第一页；翻页 → 拉对应页。
  // 单 effect 判 filterKey 变化，避免"搜索后先按旧 page 请求一次"的重复请求
  const filterKey = `${debouncedQ}|${tier}`
  const prevFilterKey = useRef(filterKey)
  useEffect(() => {
    if (prevFilterKey.current !== filterKey) {
      prevFilterKey.current = filterKey
      setPage(0)
      load(debouncedQ, tier, 0)
    } else {
      load(debouncedQ, tier, page)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey, page])

  // 返回条数 < pageSize 即末页（后端响应为裸 list，无总数；R10 后端注释同口径）
  const hasMore = users !== null && users.length >= MEMBERS_PAGE_SIZE

  const rows = useMemo(() => users ?? [], [users])

  function openPending(user: AdminUser, tier: Tier) {
    setPending({ user, tier })
    // R13-P1-2：降为 free/trial 时不预填到期时间——后端 R11-P2-2 分支会 400
    // 拒绝 free/trial + tier_expires_at 的组合；付费档位才默认 +30 天（与
    // webhook 开通语义一致），可手动改
    if (tier === 'free' || tier === 'trial') {
      setExpiresAt('')
    } else {
      setExpiresAt(toLocalInput(new Date(Date.now() + 30 * 86400000).toISOString()))
    }
  }

  async function confirmChange() {
    if (!pending) return
    setChanging(true)
    try {
      const iso = toISO(expiresAt)
      // R13-P1-2：降为 free/trial 时不传 tier_expires_at（传了后端 400 拒绝）；
      // 付费档位才传（未填则后端默认 +30 天）
      const isDowngrade = pending.tier === 'free' || pending.tier === 'trial'
      // R9-I12：展示后端实际生效值 + notices（未传到期时间时后端默认 +30 天），
      // 不再用前端自己传的 iso 渲染，避免与实际生效值不一致
      const out = await updateUserTier(
        pending.user.id,
        pending.tier,
        isDowngrade ? undefined : iso,
      )
      const rawExp = out.changes?.tier_expires_at
      const actualExp = rawExp && typeof rawExp === 'object' ? (rawExp.to ?? null) : iso
      setUsers((prev) =>
        prev?.map((u) =>
          u.id === pending.user.id
            ? { ...u, tier: pending.tier, tier_expires_at: actualExp ?? null }
            : u,
        ) ?? null,
      )
      const notices = (out.notices ?? []).length > 0 ? `（${(out.notices ?? []).join('；')}）` : ''
      toast.success(
        `已将 ${pending.user.email} 改为${TIER_LABEL[pending.tier]}${actualExp ? `（到期 ${fmtLocalTime(actualExp)}）` : ''}${notices}（已记审计）`,
      )
    } catch (e) {
      if (isAuthExpired(e)) return
      toast.error(e instanceof Error ? e.message : '改级失败')
    } finally {
      setChanging(false)
      setPending(null)
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
        <div className='mb-6'>
          <h1 className='text-2xl font-bold tracking-tight'>会员</h1>
          <p className='text-sm text-muted-foreground'>
            会员列表 / 搜索 / 按 tier 筛选 / 改级
          </p>
        </div>

        <Card className='rounded-3xl'>
          <CardHeader>
            <div className='flex flex-col gap-3 sm:flex-row sm:items-center'>
              <div className='relative flex-1'>
                <Search className='absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground' />
                <Input
                  placeholder='按邮箱搜索…'
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  className='rounded-2xl ps-9'
                />
              </div>
              <Select
                value={tier}
                onValueChange={(v) => setTier(v as 'all' | Tier)}
              >
                <SelectTrigger className='w-40 rounded-2xl'>
                  <SelectValue placeholder='全部 tier' />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value='all'>全部 tier</SelectItem>
                  {TIERS.map((t) => (
                    <SelectItem key={t} value={t}>{TIER_LABEL[t]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <CardDescription>
              {users === null
                ? loadError
                  ? '加载失败'
                  : '加载中…'
                : `共 ${users.length} 位会员（第 ${page + 1} 页）`}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {users === null ? (
              loadError ? (
                <div className='py-12 text-center'>
                  <p className='text-sm text-[#d70015]'>加载失败：{loadError}</p>
                  <button
                    onClick={() => load(debouncedQ, tier, page)}
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
                    <TableHead>邮箱</TableHead>
                    <TableHead>等级</TableHead>
                    <TableHead>管理员</TableHead>
                    <TableHead>TOTP</TableHead>
                    <TableHead>邮箱验证</TableHead>
                    <TableHead>等级到期（北京时间）</TableHead>
                    <TableHead>注册时间（北京时间）</TableHead>
                    <TableHead className='text-right'>操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((u) => (
                    <TableRow key={u.id}>
                      <TableCell className='font-medium'>{u.email}</TableCell>
                      <TableCell>
                        <Badge className={`rounded-full ${TIER_BADGE[u.tier]}`}>
                          {TIER_LABEL[u.tier]}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {u.is_admin ? (
                          <Badge className='rounded-full bg-[#d70015]/10 text-[#d70015]'>是</Badge>
                        ) : (
                          <span className='text-muted-foreground'>—</span>
                        )}
                      </TableCell>
                      <TableCell>
                        {u.totp_enabled ? (
                          <Badge className='rounded-full bg-[#34c759]/10 text-[#1f8a3d]'>已绑定</Badge>
                        ) : (
                          <span className='text-muted-foreground'>未绑定</span>
                        )}
                      </TableCell>
                      <TableCell>
                        {u.email_verified ? (
                          <Badge className='rounded-full bg-[#34c759]/10 text-[#1f8a3d]'>已验证</Badge>
                        ) : (
                          <button
                            onClick={() => setVerifying(u)}
                            className='rounded-2xl bg-[#ff9f0a]/10 px-3 py-1 text-[12px] font-medium text-[#b26a00] hover:bg-[#ff9f0a]/20'
                            title='SMTP 故障兜底：手动标记该用户邮箱已验证，后端记审计'
                          >
                            标记已验证
                          </button>
                        )}
                      </TableCell>
                      <TableCell className='font-mono text-[13px] text-muted-foreground'>
                        {fmtLocalTime(u.tier_expires_at)}
                      </TableCell>
                      <TableCell className='text-muted-foreground'>
                        {fmtLocalTime(u.created_at)}
                      </TableCell>
                      <TableCell className='text-right'>
                        <Select
                          value={u.tier}
                          onValueChange={(v) =>
                            v !== u.tier && openPending(u, v as Tier)
                          }
                        >
                          <SelectTrigger className='ml-auto w-32 rounded-2xl'>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {TIERS.map((t) => (
                              <SelectItem key={t} value={t}>{TIER_LABEL[t]}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </TableCell>
                    </TableRow>
                  ))}
                  {rows.length === 0 && (
                    <TableRow>
                      <TableCell
                        colSpan={8}
                        className='py-12 text-center text-muted-foreground'
                      >
                        {/* R12-P2-1：总数恰为 pageSize 整数倍时，下一页返回空数组，
                            此时显示"已到最后一页"而非"没有符合条件的会员" */}
                        {page > 0 ? '已到最后一页' : '没有符合条件的会员'}
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            )}
            {/* R11-P1-1：真分页器——返回条数 < pageSize 即末页（后端响应为裸 list，无总数） */}
            {users !== null && (
              <div className='mt-4 flex items-center justify-between'>
                <span className='text-sm text-muted-foreground'>第 {page + 1} 页</span>
                <div className='flex gap-2'>
                  <button
                    disabled={page === 0}
                    onClick={() => setPage((p) => p - 1)}
                    className='rounded-2xl border border-border px-4 py-1.5 text-sm transition hover:bg-muted disabled:opacity-40 disabled:hover:bg-transparent'
                  >
                    上一页
                  </button>
                  <button
                    disabled={!hasMore}
                    onClick={() => setPage((p) => p + 1)}
                    className='rounded-2xl border border-border px-4 py-1.5 text-sm transition hover:bg-muted disabled:opacity-40 disabled:hover:bg-transparent'
                  >
                    下一页
                  </button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </Main>

      {/* 改级二次确认：敏感写操作 */}
      <AlertDialog open={pending !== null} onOpenChange={(o) => !o && setPending(null)}>
        <AlertDialogContent className='rounded-3xl'>
          <AlertDialogHeader>
            <AlertDialogTitle>确认改级？</AlertDialogTitle>
            <AlertDialogDescription>
              将 <span className='font-medium text-foreground'>{pending?.user.email}</span>{' '}
              从 <span className='font-medium'>{pending && TIER_LABEL[pending.user.tier]}</span>{' '}
              改为 <span className='font-medium text-[#0071e3]'>{pending && TIER_LABEL[pending.tier]}</span>。
              该操作会立即生效并写入管理员审计日志。
            </AlertDialogDescription>
            <div className='mt-4'>
              <label className='mb-1.5 block text-sm font-medium'>
                等级到期时间（补单时手动设定；留空则不改）
              </label>
              <Input
                type='datetime-local'
                value={expiresAt}
                onChange={(e) => setExpiresAt(e.target.value)}
                className='rounded-2xl font-mono'
                // R13-P1-2：free/trial 档位无需到期时间，禁用输入避免误填触发后端 400
                disabled={pending?.tier === 'free' || pending?.tier === 'trial'}
              />
              <p className='mt-1.5 text-xs text-muted-foreground'>
                {pending?.tier === 'free' || pending?.tier === 'trial'
                  ? 'free/trial 档位无需到期时间'
                  : `当前到期：${fmtLocalTime(pending?.user.tier_expires_at ?? null)}`}
              </p>
            </div>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className='rounded-2xl'>取消</AlertDialogCancel>
            <AlertDialogAction
              className='rounded-2xl bg-[#0071e3] hover:bg-[#0071e3]/90'
              onClick={confirmChange}
              disabled={changing}
            >
              {changing ? '处理中…' : '确认改级'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* F3：手动标记邮箱已验证二次确认（SMTP 故障兜底，后端记审计） */}
      <AlertDialog open={verifying !== null} onOpenChange={(o) => !o && setVerifying(null)}>
        <AlertDialogContent className='rounded-3xl'>
          <AlertDialogHeader>
            <AlertDialogTitle>确认标记邮箱已验证？</AlertDialogTitle>
            <AlertDialogDescription>
              将 <span className='font-medium text-foreground'>{verifying?.email}</span>{' '}
              标记为邮箱已验证。该操作仅用于 SMTP 故障时的兜底，会写入管理员审计日志。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className='rounded-2xl'>取消</AlertDialogCancel>
            <AlertDialogAction
              className='rounded-2xl bg-[#0071e3] hover:bg-[#0071e3]/90'
              onClick={confirmVerifyEmail}
              disabled={verifyBusy}
            >
              {verifyBusy ? '处理中…' : '确认标记'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
