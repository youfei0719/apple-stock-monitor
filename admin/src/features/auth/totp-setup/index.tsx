import { useEffect, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { QRCodeSVG } from 'qrcode.react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { Check, Copy, Loader2, ShieldCheck } from 'lucide-react'
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
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { AuthLayout } from '../auth-layout'
import {
  totpSetup,
  totpVerify,
  AdminApiError,
  isAuthExpired,
  type TotpSetupOut,
} from '@/lib/admin-api'

const formSchema = z.object({
  code: z.string().regex(/^\d{6,8}$/, '请输入 6–8 位动态验证码'),
})

/**
 * TOTP 绑定页（P1）：新管理员首次登录后的引导流程，也可从后台头像菜单进入重绑。
 * 后端 POST /api/auth/totp/setup → {secret, uri, enabled}；
 * 二维码由 otpauth:// uri 在前端本地渲染（qrcode.react），secret 不经过任何第三方。
 * 校验走 POST /api/auth/totp/verify {code}。
 */
export function TotpSetup() {
  const navigate = useNavigate()
  const [setup, setSetup] = useState<TotpSetupOut | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [verifying, setVerifying] = useState(false)
  const [copied, setCopied] = useState(false)

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: { code: '' },
  })

  useEffect(() => {
    totpSetup()
      .then(setSetup)
      .catch(() => setLoadError(true))
  }, [])

  async function copySecret() {
    if (!setup) return
    try {
      await navigator.clipboard.writeText(setup.secret)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error('复制失败，请手动记录密钥')
    }
  }

  async function onSubmit(data: z.infer<typeof formSchema>) {
    setVerifying(true)
    try {
      await totpVerify(data.code)
      toast.success('TOTP 绑定成功，已完成二次验证')
      sessionStorage.setItem('admin-authed', '1')
      navigate({ to: '/', replace: true })
    } catch (e) {
      // R10-P2-8：会话过期已跳转登录页，静默吞掉
      if (isAuthExpired(e)) return
      toast.error(e instanceof AdminApiError ? e.message : '验证码错误，请重试')
    } finally {
      setVerifying(false)
    }
  }

  return (
    <AuthLayout>
      <Card className='w-full max-w-sm gap-4 rounded-3xl'>
        <CardHeader className='text-center'>
          <span className='mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-2xl bg-[#0071e3]/10 text-[#0071e3]'>
            <ShieldCheck className='h-6 w-6' />
          </span>
          <CardTitle className='text-lg tracking-tight'>绑定 TOTP 验证器</CardTitle>
          <CardDescription>
            用验证器 App（Authenticator / 1Password 等）扫码，
            再输入 6 位动态验证码完成绑定
          </CardDescription>
        </CardHeader>
        <CardContent>
          {setup === null && !loadError && (
            <Skeleton className='mx-auto h-48 w-48 rounded-2xl' />
          )}
          {loadError && (
            <p className='py-8 text-center text-sm text-destructive'>
              获取绑定信息失败（需要先完成邮箱 + 密码登录）
            </p>
          )}
          {setup && (
            <div className='grid gap-4'>
              <div className='mx-auto rounded-2xl border bg-white p-3'>
                <QRCodeSVG value={setup.uri} size={192} />
              </div>
              <button
                type='button'
                onClick={copySecret}
                className='mx-auto flex max-w-full items-center gap-2 rounded-2xl bg-muted px-4 py-2 font-mono text-xs break-all hover:bg-muted/70'
                title='点击复制密钥'
              >
                <span className='break-all'>{setup.secret}</span>
                {copied ? (
                  <Check className='h-3.5 w-3.5 shrink-0 text-[#34c759]' />
                ) : (
                  <Copy className='h-3.5 w-3.5 shrink-0 text-muted-foreground' />
                )}
              </button>
              <p className='text-center text-xs text-muted-foreground'>
                扫码失败时可手动输入上方密钥
              </p>
              <Form {...form}>
                <form
                  onSubmit={form.handleSubmit(onSubmit)}
                  className='grid gap-3'
                >
                  <FormField
                    control={form.control}
                    name='code'
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>动态验证码</FormLabel>
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
                    className='rounded-2xl bg-[#0071e3] hover:bg-[#0071e3]/90'
                    disabled={verifying}
                  >
                    {verifying && <Loader2 className='animate-spin' />}
                    验证并完成绑定
                  </Button>
                </form>
              </Form>
            </div>
          )}
        </CardContent>
      </Card>
    </AuthLayout>
  )
}
