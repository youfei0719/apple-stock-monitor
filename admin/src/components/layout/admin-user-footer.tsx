import { useState } from 'react'
import { LogOut, ShieldCheck } from 'lucide-react'
import { useNavigate } from '@tanstack/react-router'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import { SidebarMenu, SidebarMenuItem } from '@/components/ui/sidebar'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { USE_MOCK } from '@/lib/admin-api'
import { invalidateAdminGuardCache } from '@/routes/_authenticated/route'

/** 侧边栏底部：管理员身份 + 退出登录（前端不存任何密钥） */
export function AdminUserFooter() {
  const navigate = useNavigate()
  const [confirmOpen, setConfirmOpen] = useState(false)

  async function signOut() {
    // R9-D3：侧边栏退出同样清除路由守卫缓存（与 AdminProfile 的退出路径一致），
    // 否则 guardPassed 仍为 true，守卫放行旧会话，各接口 401 卡"加载失败"而不跳登录页
    invalidateAdminGuardCache()
    sessionStorage.removeItem('admin-authed')
    if (!USE_MOCK) {
      await fetch('/api/auth/logout', {
        method: 'POST',
        credentials: 'include',
      }).catch(() => {})
    }
    navigate({ to: '/sign-in', replace: true })
  }

  return (
    <>
      <SidebarMenu>
        <SidebarMenuItem>
          <div className='flex items-center gap-2 px-2 py-1.5'>
            <Avatar className='h-8 w-8 rounded-2xl'>
              <AvatarFallback className='rounded-2xl bg-[#0071e3] text-white'>
                <ShieldCheck className='h-4 w-4' />
              </AvatarFallback>
            </Avatar>
            <div className='grid flex-1 text-start text-sm leading-tight'>
              <span className='truncate font-semibold'>管理员</span>
              <span className='truncate text-xs text-muted-foreground'>
                后台已登录
              </span>
            </div>
            <Button
              variant='ghost'
              size='icon'
              className='rounded-xl'
              title='退出登录'
              onClick={() => setConfirmOpen(true)}
            >
              <LogOut className='h-4 w-4' />
            </Button>
          </div>
        </SidebarMenuItem>
      </SidebarMenu>
      {/* R9-D3：退出走应用内二次确认（与 AdminProfile 一致的 ConfirmDialog） */}
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title='退出登录'
        desc='确定要退出后台吗？需要重新输入密码 + TOTP 才能再次进入。'
        confirmText='退出登录'
        destructive
        handleConfirm={signOut}
        className='sm:max-w-sm'
      />
    </>
  )
}
