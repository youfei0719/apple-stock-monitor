import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { api, ApiError } from '../lib/api';
import { Card, PageHeader, PrimaryButton } from '../components/ui';

/** 注册后邮箱验证：6 位验证码 → POST /auth/verify-email */
export default function VerifyEmail() {
  const navigate = useNavigate();
  const location = useLocation();
  const initialEmail = (location.state as { email?: string } | null)?.email ?? '';
  const [email, setEmail] = useState(initialEmail);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      window.location.href = '/';
    } catch (e) {
      setError(e instanceof Error ? e.message : '验证失败');
    } finally {
      setBusy(false);
    }
  };

  const resend = async () => {
    setError(null);
    if (!email.trim()) {
      setError('请先填写注册邮箱');
      return;
    }
    setBusy(true);
    try {
      await api.resendCode(email.trim());
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setError('暂无重发入口：请返回重新注册获取新验证码');
      } else {
        setError(e instanceof Error ? e.message : '重发失败');
      }
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
          <div className="mt-5">
            <PrimaryButton onClick={submit} disabled={busy}>
              {busy ? '验证中…' : '验证并进入'}
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
