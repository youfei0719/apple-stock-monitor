import { useEffect, useMemo, useState } from 'react'
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
  getUsers,
  updateUserTier,
  type AdminUser,
  type Tier,
  TIER_LABEL,
  USE_MOCK,
} from '@/lib/admin-api'

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

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  return iso.slice(0, 16).replace('T', ' ')
}

export function Members() {
  const [users, setUsers] = useState<AdminUser[] | null>(null)
  const [q, setQ] = useState('')
  const [tier, setTier] = useState<'all' | Tier>('all')
  const [pending, setPending] = useState<PendingChange | null>(null)
  const [changing, setChanging] = useState(false)
  const [expiresAt, setExpiresAt] = useState('')

  useEffect(() => {
    setUsers(null)
    getUsers(q, tier === 'all' ? '' : tier)
      .then(setUsers)
      .catch(() => setUsers([]))
  }, [q, tier])

  const rows = useMemo(() => users ?? [], [users])

  function openPending(user: AdminUser, tier: Tier) {
    setPending({ user, tier })
    // 默认给新档位 +30 天到期（与 webhook 开通语义一致），可手动改
    setExpiresAt(toLocalInput(new Date(Date.now() + 30 * 86400000).toISOString()))
  }

  async function confirmChange() {
    if (!pending) return
    setChanging(true)
    try {
      const iso = toISO(expiresAt)
      await updateUserTier(pending.user.id, pending.tier, iso)
      setUsers((prev) =>
        prev?.map((u) =>
          u.id === pending.user.id
            ? { ...u, tier: pending.tier, tier_expires_at: iso }
            : u,
        ) ?? null,
      )
      toast.success(
        `已将 ${pending.user.email} 改为${TIER_LABEL[pending.tier]}${iso ? `（到期 ${fmtTime(iso)}）` : ''}（已记审计）`,
      )
    } catch (e) {
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
            {USE_MOCK && '（mock 数据，待后端联调）'}
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
              {users === null ? '加载中…' : `共 ${users.length} 位会员`}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {users === null ? (
              <Skeleton className='h-64 w-full rounded-2xl' />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>邮箱</TableHead>
                    <TableHead>等级</TableHead>
                    <TableHead>管理员</TableHead>
                    <TableHead>TOTP</TableHead>
                    <TableHead>等级到期</TableHead>
                    <TableHead>注册时间</TableHead>
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
                      <TableCell className='font-mono text-[13px] text-muted-foreground'>
                        {fmtTime(u.tier_expires_at)}
                      </TableCell>
                      <TableCell className='text-muted-foreground'>
                        {fmtTime(u.created_at)}
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
                        colSpan={7}
                        className='py-12 text-center text-muted-foreground'
                      >
                        没有符合条件的会员
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
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
              />
              <p className='mt-1.5 text-xs text-muted-foreground'>
                当前到期：{fmtTime(pending?.user.tier_expires_at ?? null)}
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
    </>
  )
}
