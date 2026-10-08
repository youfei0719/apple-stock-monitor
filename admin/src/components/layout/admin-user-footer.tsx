import { LogOut, ShieldCheck } from 'lucide-react'
import { useNavigate } from '@tanstack/react-router'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import { SidebarMenu, SidebarMenuItem } from '@/components/ui/sidebar'
import { USE_MOCK } from '@/lib/admin-api'

/** 侧边栏底部：管理员身份 + 退出登录（前端不存任何密钥） */
export function AdminUserFooter() {
  const navigate = useNavigate()

  async function signOut() {
    sessionStorage.removeItem('admin-authed')
    if (!USE_MOCK) {
      // TODO(后端联调): 真实登出，后端清除 HttpOnly 会话 Cookie
      await fetch('/api/auth/logout', {
        method: 'POST',
        credentials: 'include',
      }).catch(() => {})
    }
    navigate({ to: '/sign-in', replace: true })
  }

  return (
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
            onClick={signOut}
          >
            <LogOut className='h-4 w-4' />
          </Button>
        </div>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
