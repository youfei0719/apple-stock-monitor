import { useEffect, useState } from 'react'
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
  getPayments,
  type PaymentRecord,
  type Tier,
  TIER_LABEL,
  USE_MOCK,
} from '@/lib/admin-api'

// 后端 Payment.status 实际只有 'paid' 一种值（爱发电 webhook 落库）；
// 未知值直接原文展示，避免防御性映射吞掉信息。
const STATUS_LABEL: Record<string, string> = {
  paid: '已到账',
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

  useEffect(() => {
    getPayments().then(setRows).catch(() => setRows([]))
  }, [])

  const total = rows?.reduce((s, r) => s + (r.status === 'paid' ? r.amount_cny : 0), 0) ?? 0

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
            <CardTitle>付费记录</CardTitle>
            <CardDescription>按支付时间倒序</CardDescription>
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
                    <TableHead className='font-mono'>金额</TableHead>
                    <TableHead>套餐</TableHead>
                    <TableHead>等级变化</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>支付时间</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell className='font-mono text-[13px]'>
                        {r.order_id}
                      </TableCell>
                      <TableCell className='font-mono text-[13px] text-muted-foreground'>
                        {r.user_id ?? '—'}
                      </TableCell>
                      <TableCell className='font-mono font-semibold'>
                        ¥{r.amount_cny}
                      </TableCell>
                      <TableCell className='font-mono text-[13px] text-muted-foreground'>
                        {r.plan}
                      </TableCell>
                      <TableCell>{fmtTierChange(r)}</TableCell>
                      <TableCell>
                        <Badge
                          className={`rounded-full ${r.status === 'paid' ? 'bg-[#34c759]/10 text-[#1f8a3d]' : 'bg-[#e8e8ed] text-[#6e6e73]'}`}
                        >
                          {STATUS_LABEL[r.status] ?? r.status}
                        </Badge>
                      </TableCell>
                      <TableCell className='text-muted-foreground'>
                        {fmtTime(r.created_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                  {rows.length === 0 && (
                    <TableRow>
                      <TableCell
                        colSpan={7}
                        className='py-12 text-center text-muted-foreground'
                      >
                        暂无付费记录
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
