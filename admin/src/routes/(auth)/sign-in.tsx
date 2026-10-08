import { z } from 'zod'
import { createFileRoute } from '@tanstack/react-router'
import { SignIn } from '@/features/auth/sign-in'

const searchSchema = z.object({
  redirect: z.string().optional(),
  // P2：reason=forbidden 表示已登录但无管理员权限（路由守卫带过来的），页面给出明确提示
  reason: z.string().optional(),
})

export const Route = createFileRoute('/(auth)/sign-in')({
  component: SignIn,
  validateSearch: searchSchema,
})
