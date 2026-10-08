import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { api, ApiError } from '../lib/api';
import { Card, PageHeader, PrimaryButton } from '../components/ui';

/** 注册后邮箱验证：6 位验证码 → POST /auth/verify-email */
export default function VerifyEmail() {
  const navigate = useNavigate();
  const location = useLocation();
  const locState = (location.state as { email?: string; notice?: string | null } | null);
  const initialEmail = locState?.email ?? '';
  const [email, setEmail] = useState(initialEmail);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // R6-U1：重发成功给一条成功提示（之前零反馈，用户不知道有没有发出去）
  const [resentMsg, setResentMsg] = useState<string | null>(null);

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
      // F-3：后端验证成功不 Set-Cookie，跳首页会显示匿名态误导用户 → 跳登录页并提示
      // R7：注册时的匿名任务认领提示（notice）链到登录页，登录成功后统一带到首页
      navigate('/login', {
        state: { email: email.trim(), message: '验证成功，请登录', notice: locState?.notice ?? null },
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
    setError(null);
    setResentMsg(null);
    if (!email.trim()) {
      setError('请先填写注册邮箱');
      return;
    }
    setBusy(true);
    try {
      await api.resendCode(email.trim());
      setResentMsg('验证码已重新发送，请查收邮箱');
    } catch (e) {
      // F-N8：后端 resend-code 防枚举永远 200，404 死分支已删除
      setError(e instanceof Error ? e.message : '重发失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageHeader title="验证邮箱" subtitle="6 位验证码已发送到你的邮箱" />
      <div className="px-6">
        <Card className="p-6 rise-in">
          <div className="space-y-3">
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="注册邮箱"
              autoComplete="email"
              className="w-full px-4 py-3 rounded-card-sm bg-bg text-[15px] outline-none placeholder:text-faint"
            />
            <input
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              inputMode="numeric"
              placeholder="6 位验证码"
              autoComplete="one-time-code"
              onKeyDown={(e) => e.key === 'Enter' && submit()}
              className="w-full px-4 py-3 rounded-card-sm bg-bg text-[15px] mono text-center tracking-[0.5em] outline-none placeholder:text-faint placeholder:tracking-normal"
            />
          </div>
          {error && <p className="mt-3 text-sm text-bad text-center">{error}</p>}
          {resentMsg && <p className="mt-3 text-sm text-ok text-center">{resentMsg}</p>}
          <div className="mt-5">
            <PrimaryButton onClick={submit} disabled={busy}>
              {busy ? '验证中…' : '验证'}
            </PrimaryButton>
          </div>
          <button
            onClick={resend}
            disabled={busy}
            className="mt-3 w-full text-center text-sm text-accent font-medium disabled:opacity-40"
          >
            没收到？重新发送验证码
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
