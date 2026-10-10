import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { Card, PageHeader, PrimaryButton } from '../components/ui';

export default function ResetPassword() {
  const location = useLocation(), navigate = useNavigate();
  const [email, setEmail] = useState((location.state as { email?: string } | null)?.email ?? '');
  const [code, setCode] = useState(''), [password, setPassword] = useState(''), [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false), [sending, setSending] = useState(false), [cooldown, setCooldown] = useState(0);
  const [error, setError] = useState<string | null>(null), [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setTimeout(() => setCooldown(cooldown - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [cooldown]);
  const requestCode = async () => {
    if (sending || cooldown > 0) return;
    setError(null); setMessage(null);
    if (!email.trim()) { setError('请填写注册邮箱'); return; }
    setSending(true);
    try { const result = await api.requestPasswordReset(email.trim()); setMessage(result.message); setCooldown(60); }
    catch (e) { setError(e instanceof Error ? e.message : '发送失败，请重试'); }
    finally { setSending(false); }
  };
  const reset = async () => {
    if (busy) return;
    setError(null);
    if (!email.trim() || !/^\d{6}$/.test(code)) { setError('请填写邮箱和 6 位验证码'); return; }
    if (password.length < 8 || !/[a-zA-Z]/.test(password) || !/\d/.test(password)) { setError('新密码至少 8 位，包含字母和数字'); return; }
    if (password !== confirm) { setError('两次密码不一致'); return; }
    setBusy(true);
    try {
      await api.confirmPasswordReset(email.trim(), code, password);
      navigate('/login', { replace: true, state: { email: email.trim(), message: '密码已重置，请重新登录' } });
    } catch (e) { setError(e instanceof Error ? e.message : '重置失败，请重试'); }
    finally { setBusy(false); }
  };
  return <div>
    <PageHeader title="找回密码" subtitle="通过注册邮箱重置" />
    <Card className="mx-5 p-5 space-y-4">
      <label className="block text-sm">注册邮箱<input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className="mt-1 w-full p-3 rounded-card-sm bg-bg" /></label>
      <button onClick={requestCode} disabled={sending || cooldown > 0} className="text-sm text-accent disabled:opacity-40">{sending ? '发送中…' : cooldown > 0 ? `${cooldown} 秒后可重新发送` : '获取重置验证码'}</button>
      {message && <p role="status" className="text-sm text-sub">{message}</p>}
      <label className="block text-sm">6 位重置验证码<input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))} className="mt-1 w-full p-3 rounded-card-sm bg-bg" /></label>
      <label className="block text-sm">新密码（至少 8 位，含字母和数字）<input type="password" maxLength={128} autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} className="mt-1 w-full p-3 rounded-card-sm bg-bg" /></label>
      <label className="block text-sm">确认新密码<input type="password" maxLength={128} autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !busy) void reset(); }} className="mt-1 w-full p-3 rounded-card-sm bg-bg" /></label>
      {error && <p role="alert" className="text-sm text-bad">{error}</p>}
      <PrimaryButton onClick={reset} disabled={busy}>{busy ? '重置中…' : '重置密码'}</PrimaryButton>
      <p className="text-xs text-sub">验证码 10 分钟有效。重置后，所有设备需重新登录。</p>
      <Link to="/login" className="block text-sm text-accent">返回登录</Link>
    </Card>
  </div>;
}
