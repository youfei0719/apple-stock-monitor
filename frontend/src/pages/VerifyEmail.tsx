import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { api, ApiError } from '../lib/api';
import { Card, PageHeader, PrimaryButton } from '../components/ui';

type VerifyContext = { email?: string; notice?: string | null; emailSent?: boolean; returnTo?: string; resendUntil?: number };
const VERIFY_CONTEXT_KEY = 'stockmon.verify_context';
function savedContext(): VerifyContext | null {
  try { return JSON.parse(sessionStorage.getItem(VERIFY_CONTEXT_KEY) ?? 'null') as VerifyContext | null; }
  catch { return null; }
}
function saveContext(value: VerifyContext | null) {
  try { if (value) sessionStorage.setItem(VERIFY_CONTEXT_KEY, JSON.stringify({ email: value.email, notice: value.notice, emailSent: value.emailSent, returnTo: value.returnTo, resendUntil: value.resendUntil })); else sessionStorage.removeItem(VERIFY_CONTEXT_KEY); }
  catch { /* 禁用存储时仍可手动填写邮箱 */ }
}

/** 注册后邮箱验证：6 位验证码 → POST /auth/verify-email */
export default function VerifyEmail() {
  const navigate = useNavigate();
  const location = useLocation();
  const locState = (location.state as VerifyContext | null) ?? savedContext();
  const initialEmail = locState?.email ?? '';
  // R9-I15：后端 register 返回 email_sent=false（SMTP 瞬断导致验证码首发失败）时，
  // 别让用户干等一封从未发出的邮件——明确提示点"重新发送验证码"
  const [emailSent, setEmailSent] = useState(locState?.emailSent ?? true);
  const [email, setEmail] = useState(initialEmail);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // R6-U1：重发成功给一条成功提示（之前零反馈，用户不知道有没有发出去）
  const [resentMsg, setResentMsg] = useState<string | null>(null);
  // P0：重发按钮独立 loading＋60s 冷却——点了立刻有反馈，防连点刷接口
  const [resending, setResending] = useState(false);
  const [resendUntil, setResendUntil] = useState(() => {
    const saved = savedContext();
    if (saved?.email === initialEmail && saved.resendUntil) return saved.resendUntil;
    return emailSent && initialEmail ? Date.now() + 60000 : 0;
  });
  const [cooldown, setCooldown] = useState(() => Math.max(0, Math.ceil((resendUntil - Date.now()) / 1000)));

  useEffect(() => {
    saveContext({ ...locState, email, emailSent, resendUntil });
  }, [email, emailSent, resendUntil]);
  useEffect(() => {
    const update = () => setCooldown(Math.max(0, Math.ceil((resendUntil - Date.now()) / 1000)));
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [resendUntil]);

  const submit = async () => {
    setError(null);
    if (!email.trim()) {
      setError('请填写注册邮箱');
      return;
    }
    if (!/^\d{6}$/.test(code.trim())) {
      setError('请输入 6 位数字验证码');
      return;
    }
    setBusy(true);
    try {
      await api.verifyEmail(email.trim(), code.trim());
      saveContext(null);
      // F-3：后端验证成功不 Set-Cookie，跳首页会显示匿名态误导用户 → 跳登录页并提示
      // R7：注册时的匿名任务认领提示（notice）链到登录页，登录成功后统一带到首页
      navigate('/login', {
        state: { email: email.trim(), returnTo: locState?.returnTo, message: '验证成功，请登录', notice: locState?.notice ?? null },
        replace: true,
      });
    } catch (e) {
      // 后端验证码过期返回 code=code_expired，前端给统一的用户提示（N4）
      if (e instanceof ApiError && e.code === 'code_expired') {
        setError('验证码已过期，请重新获取');
      } else {
        setError(e instanceof Error ? e.message : '验证失败');
      }
    } finally {
      setBusy(false);
    }
  };

  const resend = async () => {
    if (resending || cooldown > 0) return;
    setError(null);
    setResentMsg(null);
    if (!email.trim()) {
      setError('请先填写注册邮箱');
      return;
    }
    setResending(true);
    try {
      await api.resendCode(email.trim());
      setResentMsg('验证码已重新发送，请查收邮箱');
      setEmailSent(true);
      setResendUntil(Date.now() + 60000);
      setCooldown(60);
    } catch (e) {
      // F-N8：后端 resend-code 防枚举永远 200，404 死分支已删除
      setError(e instanceof Error ? e.message : '重发失败');
    } finally {
      setResending(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="验证邮箱"
        subtitle={emailSent ? '6 位验证码已发送到你的邮箱' : '验证码发送失败，请重新发送'}
      />
      <div className="px-6">
        <Card className="p-6 rise-in">
          {/* R9-I15：验证码首发失败（email_sent=false）时醒目提示，别让用户干等 */}
          {!emailSent && (
            <p className="mb-4 rounded-card-sm bg-[#fff7e8] border border-[#f0c36d] px-3 py-2.5 text-xs text-[#b25e09] text-center leading-relaxed">
              邮件未发出，请重新发送。
            </p>
          )}
          <div className="space-y-3">
            <input
              aria-label="注册邮箱"
              type="email"
              value={email}
              onChange={(e) => { setEmail(e.target.value); setCode(''); setResendUntil(0); }}
              placeholder="注册邮箱"
              autoComplete="email"
              className="w-full px-4 py-3 rounded-card-sm bg-bg text-[15px] outline-none placeholder:text-faint"
            />
            <input
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              aria-label="6 位验证码"
              inputMode="numeric"
              placeholder="6 位验证码"
              autoComplete="one-time-code"
              onKeyDown={(e) => e.key === 'Enter' && submit()}
              className="w-full px-4 py-3 rounded-card-sm bg-bg text-[15px] mono text-center tracking-[0.5em] outline-none placeholder:text-faint placeholder:tracking-normal"
            />
          </div>
          {error && <p role="alert" className="mt-3 text-sm text-bad text-center">{error}</p>}
          {resentMsg && <p className="mt-3 text-sm text-ok text-center">{resentMsg}</p>}
          <div className="mt-5">
            <PrimaryButton onClick={submit} disabled={busy || !email.trim() || !/^\d{6}$/.test(code.trim())}>
              {busy ? '验证中…' : '验证'}
            </PrimaryButton>
          </div>
          <button
            onClick={resend}
            disabled={resending || cooldown > 0}
            className="mt-3 w-full text-center text-sm text-accent font-medium disabled:opacity-40"
          >
            {resending ? '发送中…' : cooldown > 0 ? `${cooldown} 秒后可重发` : '没收到？重新发送验证码'}
          </button>
          <button
            onClick={() => navigate('/login', { replace: true })}
            className="mt-2 w-full text-center text-xs text-faint"
          >
            返回登录
          </button>
        </Card>
      </div>
    </div>
  );
}
