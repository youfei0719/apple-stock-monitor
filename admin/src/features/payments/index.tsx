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
  TIER_LABEL,
  USE_MOCK,
} from '@/lib/admin-api'

const STATUS_BADGE: Record<PaymentRecord['status'], string> = {
  success: 'bg-[#34c759]/10 text-[#1f8a3d]',
  pending: 'bg-[#ff9f0a]/10 text-[#b26a00]',
  refunded: 'bg-[#e8e8ed] text-[#6e6e73]',
}
const STATUS_LABEL: Record<PaymentRecord['status'], string> = {
  success: '已开通',
  pending: '待确认',
  refunded: '已退款',
}

export function Payments() {
  const [rows, setRows] = useState<PaymentRecord[] | null>(null)

  useEffect(() => {
    getPayments().then(setRows).catch(() => setRows([]))
  }, [])

  const total = rows?.reduce((s, r) => s + (r.status === 'success' ? r.amountCny : 0), 0) ?? 0

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
                    <TableHead>用户邮箱</TableHead>
                    <TableHead className='font-mono'>金额</TableHead>
                    <TableHead>开通等级</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>支付时间</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell className='font-mono text-[13px]'>
                        {r.afdianOrderNo}
                      </TableCell>
                      <TableCell className='font-medium'>{r.email}</TableCell>
                      <TableCell className='font-mono font-semibold'>
                        ¥{r.amountCny}
                      </TableCell>
                      <TableCell>{TIER_LABEL[r.tier]}</TableCell>
                      <TableCell>
                        <Badge className={`rounded-full ${STATUS_BADGE[r.status]}`}>
                          {STATUS_LABEL[r.status]}
                        </Badge>
                      </TableCell>
                      <TableCell className='text-muted-foreground'>
                        {r.paidAt.slice(0, 16).replace('T', ' ')}
                      </TableCell>
                    </TableRow>
                  ))}
                  {rows.length === 0 && (
                    <TableRow>
                      <TableCell
                        colSpan={6}
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
