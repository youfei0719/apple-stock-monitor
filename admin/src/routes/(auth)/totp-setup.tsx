import { createFileRoute } from '@tanstack/react-router'
import { TotpSetup } from '@/features/auth/totp-setup'

/**
 * TOTP 绑定页：(auth) 分组，无后台守卫——新管理员登录后、
 * TOTP 会话尚未验证时也能进入（只依赖登录 Cookie）。
 */
export const Route = createFileRoute('/(auth)/totp-setup')({
  component: TotpSetup,
})
