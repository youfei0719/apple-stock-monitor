import { useState } from 'react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { AlertTriangle, ArrowLeft, Loader2, LogIn, ShieldCheck } from 'lucide-react'
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
import {
  adminLoginPassword,
  getMe,
  totpVerify,
  AdminApiError,
  USE_MOCK,
} from '@/lib/admin-api'

const step1Schema = z.object({
  email: z.string().email('请输入有效的管理员邮箱'),
  password: z.string().min(1, '请输入管理员密码'),
})
const step2Schema = z.object({
  totp: z.string().regex(/^\d{6,8}$/, '请输入 6–8 位动态验证码'),
})

// 与后端契约一致：连续失败触发后端锁定（config.py: LOGIN_FAIL_LOCK=5 / LOGIN_LOCK_MINUTES=15）。
// 第 1 步（邮箱 + 密码）按 IP 计锁；第 2 步（TOTP）按账号（user）计锁。
const MAX_FAILS = 5
// R9-I5：前端页面冷却（防连点 UX），真实锁定由后端执行——后端锁 15 分钟，
// 文案不许暗示"60 秒后锁定就解除"，否则 60 秒后重试必吃 429 陷入死循环。
const COOLDOWN_SEC = 60

/**
 * 后台登录（两步，对齐后端 auth.py）：
 *  1. 邮箱 + 密码 → POST /api/auth/login → {ok, totp_required} + Set-Cookie
 *  2. totp_required=false → 直接进后台；
 *     已绑定 TOTP → 输验证码走 POST /api/auth/totp/verify；
 *     未绑定（新管理员）→ 跳 /totp-setup 扫码绑定后再进。
 * 校验全部在后端；前端只做失败计数与限流提示（防爆破 UX），不存任何密钥。
 */
export function SignIn() {
  const { redirect, reason } = useSearch({ from: '/(auth)/sign-in' })
  const navigate = useNavigate()
  const [step, setStep] = useState<1 | 2>(1)
  const [isLoading, setIsLoading] = useState(false)
  const [failCount, setFailCount] = useState(0)
  const [cooldown, setCooldown] = useState(0)

  const form1 = useForm<z.infer<typeof step1Schema>>({
    resolver: zodResolver(step1Schema),
    defaultValues: { email: '', password: '' },
  })
  const form2 = useForm<z.infer<typeof step2Schema>>({
    resolver: zodResolver(step2Schema),
    defaultValues: { totp: '' },
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

  /** 失败计数（登录/验证码失败共用；连续 MAX_FAILS 次触发前端冷却） */
  function registerFail(e: unknown, fallbackMsg: string) {
    const n = failCount + 1
    setFailCount(n)
    toast.error(e instanceof AdminApiError ? e.message : fallbackMsg)
    if (n >= MAX_FAILS) startCooldown()
  }

  function loginDone() {
    // 登录成功：mock 模式记一个本地标记；真实模式靠 HttpOnly Cookie。
    // 前端不存任何 token / 密钥。
    sessionStorage.setItem('admin-authed', '1')
    setFailCount(0)
    toast.success('登录成功')
    navigate({ to: redirect || '/', replace: true })
  }

  async function onSubmitStep1(data: z.infer<typeof step1Schema>) {
    if (cooldown > 0) return
    setIsLoading(true)
    try {
      const login = await adminLoginPassword(data.email, data.password)
      if (!login.totp_required) {
        loginDone()
        return
      }
      // 管理员会话需要 TOTP 二次验证：已绑定 → 第 2 步输码；未绑定 → 引导绑定
      const me = await getMe()
      if (!me.totp_enabled) {
        sessionStorage.setItem('admin-authed', '1')
        toast.info('首次登录：请先绑定 TOTP 动态验证码')
        navigate({ to: '/totp-setup', replace: true })
        return
      }
      setStep(2)
    } catch (e) {
      registerFail(e, '登录失败，请检查邮箱与密码')
    } finally {
      setIsLoading(false)
    }
  }

  async function onSubmitStep2(data: z.infer<typeof step2Schema>) {
    if (cooldown > 0) return
    setIsLoading(true)
    try {
      await totpVerify(data.totp)
      loginDone()
    } catch (e) {
      registerFail(e, '验证码错误，请重试')
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
            {step === 1
              ? '第 1 步：邮箱 + 密码'
              : '第 2 步：TOTP 动态验证码'}
            {USE_MOCK && '（mock：任意邮箱密码可登录；验证码填 000000 可模拟失败）'}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {reason === 'forbidden' && (
            <div className='mb-4 flex items-start gap-2 rounded-2xl bg-[#d70015]/10 px-4 py-3 text-sm text-[#d70015]'>
              <AlertTriangle className='mt-0.5 h-4 w-4 shrink-0' />
              <div>
                该账号无管理员权限。
                <br />
                请使用管理员账号登录，或联系管理员开通。
              </div>
            </div>
          )}
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
                  step === 1 ? (
                    <>
                      已连续 {MAX_FAILS} 次登录失败：当前 IP 将被后端锁定约 15 分钟。
                      <br />
                      页面冷却 {cooldown} 秒（锁定期内重试也会被拒绝，请确认密码无误后再试）。
                    </>
                  ) : (
                    <>
                      已连续 {MAX_FAILS} 次验证码失败：该账号将被后端锁定约 15
                      分钟（按账号计，非 IP）。
                      <br />
                      页面冷却 {cooldown} 秒（锁定期内重试也会被拒绝）。
                    </>
                  )
                ) : step === 1 ? (
                  <>
                    登录失败（第 {failCount} 次）。连续 {MAX_FAILS}{' '}
                    次失败将锁定当前 IP 约 15 分钟。
                  </>
                ) : (
                  <>
                    验证码失败（第 {failCount} 次）。连续 {MAX_FAILS}{' '}
                    次失败将锁定该账号约 15 分钟（按账号计，非 IP）。
                  </>
                )}
              </div>
            </div>
          )}
          {step === 1 ? (
            <Form {...form1}>
              <form
                onSubmit={form1.handleSubmit(onSubmitStep1)}
                className='grid gap-4'
              >
                <FormField
                  control={form1.control}
                  name='email'
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>管理员邮箱</FormLabel>
                      <FormControl>
                        <Input
                          placeholder='admin@example.com'
                          type='email'
                          autoComplete='username'
                          className='rounded-2xl'
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form1.control}
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
                <Button
                  className='mt-1 rounded-2xl bg-[#0071e3] hover:bg-[#0071e3]/90'
                  disabled={isLoading || locked}
                >
                  {isLoading ? (
                    <Loader2 className='animate-spin' />
                  ) : (
                    <LogIn />
                  )}
                  {locked ? `${cooldown} 秒后重试` : '下一步'}
                </Button>
              </form>
            </Form>
          ) : (
            <Form {...form2}>
              <form
                onSubmit={form2.handleSubmit(onSubmitStep2)}
                className='grid gap-4'
              >
                <FormField
                  control={form2.control}
                  name='totp'
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>TOTP 动态验证码</FormLabel>
                      <FormControl>
                        <Input
                          placeholder='6 位数字'
                          inputMode='numeric'
                          maxLength={8}
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
                <Button
                  type='button'
                  variant='ghost'
                  className='rounded-2xl'
                  disabled={isLoading}
                  onClick={() => setStep(1)}
                >
                  <ArrowLeft className='h-4 w-4' />
                  返回上一步
                </Button>
              </form>
            </Form>
          )}
        </CardContent>
      </Card>
    </AuthLayout>
  )
}
