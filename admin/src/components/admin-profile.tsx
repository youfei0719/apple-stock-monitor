import { useState } from 'react'
import { KeyRound, LogOut, ShieldCheck } from 'lucide-react'
import { useNavigate } from '@tanstack/react-router'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { invalidateAdminGuardCache } from '@/routes/_authenticated/route'

/** 后台管理员头像菜单：退出登录（前端不存任何密钥） */
export function AdminProfile() {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()

  async function handleSignOut() {
    // R8-U-12：退出后清除路由守卫缓存，避免旧登录态被复用
    // R11-P2-6：mock 分支已删——只走真实登出（后端清除 HttpOnly 会话 Cookie）
    invalidateAdminGuardCache()
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }).catch(() => {})
    navigate({ to: '/sign-in', replace: true })
  }

  return (
    <>
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button variant='ghost' className='relative h-9 w-9 rounded-full'>
            <Avatar className='h-9 w-9'>
              <AvatarFallback className='bg-[#0071e3] text-white'>
                <ShieldCheck className='h-4 w-4' />
              </AvatarFallback>
            </Avatar>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className='w-56 rounded-2xl' align='end' forceMount>
          <DropdownMenuLabel className='font-normal'>
            <div className='flex flex-col gap-1.5'>
              <p className='text-sm leading-none font-medium'>管理员</p>
              <p className='text-xs leading-none text-muted-foreground'>
                stock.glint.red/admin
              </p>
            </div>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onClick={() => navigate({ to: '/totp-setup' })}
          >
            <KeyRound className='me-2 h-4 w-4' />
            TOTP 绑定 / 重绑
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => setOpen(true)} className='text-destructive'>
            <LogOut className='me-2 h-4 w-4' />
            退出登录
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title='退出登录'
        desc='确定要退出后台吗？需要重新输入密码 + TOTP 才能再次进入。'
        confirmText='退出登录'
        destructive
        handleConfirm={handleSignOut}
        className='sm:max-w-sm'
      />
    </>
  )
}
