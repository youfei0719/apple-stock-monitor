import { createFileRoute, redirect } from '@tanstack/react-router'
import { AuthenticatedLayout } from '@/components/layout/authenticated-layout'
import { USE_MOCK } from '@/lib/admin-api'

/**
 * 后台路由守卫：未登录一律去 /sign-in。
 * mock 模式用 sessionStorage 标记；真实模式用 HttpOnly Cookie 会话，
 * 以轻量管理接口校验（401/403 → 登录页）。前端不存任何密钥。
 */
export const Route = createFileRoute('/_authenticated')({
  beforeLoad: async ({ location }) => {
    const toSignIn = (): never => {
      throw redirect({
        to: '/sign-in',
        search: { redirect: location.href },
      })
    }
    if (USE_MOCK) {
      if (sessionStorage.getItem('admin-authed') !== '1') toSignIn()
      return
    }
    let ok = false
    try {
      const res = await fetch('/api/admin/overview', { credentials: 'include' })
      ok = res.ok
    } catch {
      ok = false
    }
    if (!ok) toSignIn()
  },
  component: AuthenticatedLayout,
})
