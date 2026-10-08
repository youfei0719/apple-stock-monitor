import { useState } from 'react';
import { api } from '../lib/api';
import { Card, PageHeader, PrimaryButton } from '../components/ui';

export default function Login() {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    if (!email.trim() || !password) {
      setError('请填写邮箱和密码');
      return;
    }
    setBusy(true);
    try {
      if (mode === 'login') await api.login(email.trim(), password);
      else await api.register(email.trim(), password);
      window.location.href = '/';
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageHeader title="DING" subtitle="Apple 直营店库存监控" />
      <div className="px-6">
        <Card className="p-6 rise-in">
          <div className="grid grid-cols-2 gap-2 mb-5 p-1 rounded-pill bg-bg">
            {(
              [
                { id: 'login', label: '登录' },
                { id: 'register', label: '注册' },
              ] as const
            ).map((m) => (
              <button
                key={m.id}
                onClick={() => setMode(m.id)}
                className={`py-2 rounded-pill text-sm font-medium transition ${
                  mode === m.id ? 'bg-white shadow-card text-ink' : 'text-sub'
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
          <div className="space-y-3">
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="邮箱"
              autoComplete="email"
              className="w-full px-4 py-3 rounded-card-sm bg-bg text-[15px] outline-none placeholder:text-faint"
            />
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="密码"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              onKeyDown={(e) => e.key === 'Enter' && submit()}
              className="w-full px-4 py-3 rounded-card-sm bg-bg text-[15px] outline-none placeholder:text-faint"
            />
          </div>
          {error && <p className="mt-3 text-sm text-bad text-center">{error}</p>}
          <div className="mt-5">
            <PrimaryButton onClick={submit} disabled={busy}>
              {busy ? '请稍候…' : mode === 'login' ? '登录' : '注册并登录'}
            </PrimaryButton>
          </div>
        </Card>
        <p className="mt-4 text-center text-xs text-faint">
          注册即开通体验会员 · 连续 5 次登录失败将锁定 IP 15 分钟
        </p>
      </div>
    </div>
  );
}
