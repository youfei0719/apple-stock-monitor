import { createFileRoute, redirect } from '@tanstack/react-router'
import { AuthenticatedLayout } from '@/components/layout/authenticated-layout'
import { USE_MOCK } from '@/lib/admin-api'

/**
 * 后台路由守卫：未登录一律去 /sign-in。
 * mock 模式用 sessionStorage 标记；真实模式用 HttpOnly Cookie 会话，
 * 以管理接口校验（401/403 → 登录页）。前端不存任何密钥。
 *
 * TOTP 分流：会话有效但未过 TOTP 二次验证（后端 code=totp_required）时，
 * 未绑定 TOTP 的新管理员 → /totp-setup 扫码绑定；
 * 已绑定的 → 回 /sign-in 重新走"密码 + TOTP"登录。
 *
 * R8-U-12：守卫结果同会话缓存——overview 一次通过后不再重复请求；
 * 退出登录时由 AdminProfile 调 invalidateAdminGuardCache() 清除。
 */
let guardPassed = false
export function invalidateAdminGuardCache() {
  guardPassed = false
}

export const Route = createFileRoute('/_authenticated')({
  beforeLoad: async ({ location }) => {
    const toSignIn = (reason?: string): never => {
      throw redirect({
        to: '/sign-in',
        search: { redirect: location.href, reason },
      })
    }
    if (USE_MOCK) {
      if (sessionStorage.getItem('admin-authed') !== '1') toSignIn()
      return
    }
    // R8-U-12：同会话内守卫已通过，直接放行，跳过重复的 /api/admin/overview 请求
    if (guardPassed) return
    const fetchMe = () =>
      fetch('/api/me', { credentials: 'include' })
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null)
    // 标记位在 try 外消费，避免 catch 吞掉 redirect（旧代码的坑）
    let goTotpSetup = false
    let forbidden = false
    try {
      const res = await fetch('/api/admin/overview', { credentials: 'include' })
      if (res.ok) {
        guardPassed = true
        return
      }
      if (res.status === 403) {
        const body = await res.json().catch(() => ({}))
        if (body.code === 'totp_required') {
          // 会话有效，仅缺 TOTP：查绑定状态决定去向
          const me = await fetchMe()
          if (me && me.totp_enabled === false) goTotpSetup = true
        } else {
          // P2：已登录但非管理员——不再静默踢回，给出明确提示
          const me = await fetchMe()
          if (me && me.email) forbidden = true
        }
      }
    } catch {
      // 网络异常等同未授权处理
    }
    if (goTotpSetup) throw redirect({ to: '/totp-setup' })
    toSignIn(forbidden ? 'forbidden' : undefined)
  },
  component: AuthenticatedLayout,
})
