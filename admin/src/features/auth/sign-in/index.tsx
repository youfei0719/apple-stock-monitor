import { useState } from 'react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { AlertTriangle, Loader2, LogIn, ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { PasswordInput } from '@/components/password-input'
import { Button } from '@/components/ui/button'
import { AuthLayout } from '../auth-layout'
import { adminLogin, AdminApiError, USE_MOCK } from '@/lib/admin-api'

const formSchema = z.object({
  password: z.string().min(1, '请输入管理员密码'),
  totp: z.string().regex(/^\d{6}$/, '请输入 6 位动态验证码'),
})

const MAX_FAILS = 5 // 与后端契约一致：连续 5 次失败锁 IP 15 分钟
const COOLDOWN_SEC = 60 // 前端侧演示冷却（真实 15 分钟锁定由后端执行）

/**
 * 后台登录：密码 + TOTP 两步。
 * 校验全部在后端；前端只做失败计数与限流提示（防爆破 UX），不存任何密钥。
 */
export function SignIn() {
  const { redirect } = useSearch({ from: '/(auth)/sign-in' })
  const navigate = useNavigate()
  const [isLoading, setIsLoading] = useState(false)
  const [failCount, setFailCount] = useState(0)
  const [cooldown, setCooldown] = useState(0)

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: { password: '', totp: '' },
  })

  function startCooldown() {
    setCooldown(COOLDOWN_SEC)
    const timer = setInterval(() => {
      setCooldown((c) => {
        if (c <= 1) {
          clearInterval(timer)
          setFailCount(0)
          return 0
        }
        return c - 1
      })
    }, 1000)
  }

  async function onSubmit(data: z.infer<typeof formSchema>) {
    if (cooldown > 0) return
    setIsLoading(true)
    try {
      await adminLogin(data.password, data.totp)
      // 登录成功：mock 模式记一个本地标记；真实模式靠 HttpOnly Cookie。
      // 前端不存任何 token / 密钥。
      sessionStorage.setItem('admin-authed', '1')
      setFailCount(0)
      toast.success('登录成功')
      navigate({ to: redirect || '/', replace: true })
    } catch (e) {
      const n = failCount + 1
      setFailCount(n)
      const msg =
        e instanceof AdminApiError ? e.message : '登录失败，请检查密码与验证码'
      toast.error(msg)
      if (n >= MAX_FAILS) {
        // 连续 5 次失败：按契约后端会锁 IP 15 分钟；前端同时冷却 60 秒防继续爆破
        startCooldown()
      }
    } finally {
      setIsLoading(false)
    }
  }

  const locked = cooldown > 0

  return (
    <AuthLayout>
      <Card className='w-full max-w-sm gap-4 rounded-3xl'>
        <CardHeader className='text-center'>
          <span className='mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-2xl bg-[#0071e3]/10 text-[#0071e3]'>
            <ShieldCheck className='h-6 w-6' />
          </span>
          <CardTitle className='text-lg tracking-tight'>管理员登录</CardTitle>
          <CardDescription>
            密码 + TOTP 两步验证
            {USE_MOCK && '（mock 模式：任意密码可登录；验证码填 000000 可模拟失败）'}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {(failCount > 0 || locked) && (
            <div
              className={`mb-4 flex items-start gap-2 rounded-2xl px-4 py-3 text-sm ${
                locked
                  ? 'bg-[#d70015]/10 text-[#d70015]'
                  : 'bg-[#ff9f0a]/10 text-[#b26a00]'
              }`}
            >
              <AlertTriangle className='mt-0.5 h-4 w-4 shrink-0' />
              <div>
                {locked ? (
                  <>
                    已连续 {MAX_FAILS} 次失败，触发限流：当前 IP 将被锁定 15
                    分钟。
                    <br />
                    请 {cooldown} 秒后再试。
                  </>
                ) : (
                  <>
                    登录失败（第 {failCount} 次）。连续 {MAX_FAILS}{' '}
                    次失败将锁定当前 IP 15 分钟。
                  </>
                )}
              </div>
            </div>
          )}
          <Form {...form}>
            <form
              onSubmit={form.handleSubmit(onSubmit)}
              className='grid gap-4'
            >
              <FormField
                control={form.control}
                name='password'
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>管理员密码</FormLabel>
                    <FormControl>
                      <PasswordInput
                        placeholder='请输入密码'
                        autoComplete='current-password'
                        className='rounded-2xl'
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name='totp'
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>TOTP 动态验证码</FormLabel>
                    <FormControl>
                      <Input
                        placeholder='6 位数字'
                        inputMode='numeric'
                        maxLength={6}
                        autoComplete='one-time-code'
                        className='rounded-2xl font-mono tracking-[0.3em]'
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <Button
                className='mt-1 rounded-2xl bg-[#0071e3] hover:bg-[#0071e3]/90'
                disabled={isLoading || locked}
              >
                {isLoading ? (
                  <Loader2 className='animate-spin' />
                ) : (
                  <LogIn />
                )}
                {locked ? `${cooldown} 秒后重试` : '登录'}
              </Button>
            </form>
          </Form>
        </CardContent>
      </Card>
    </AuthLayout>
  )
}
