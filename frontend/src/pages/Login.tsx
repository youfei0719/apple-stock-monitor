import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { api, ApiError } from '../lib/api';
import { useApp } from '../components/App';
import { Card, PageHeader, PrimaryButton } from '../components/ui';

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const { refreshMe } = useApp();
  // F-3：邮箱验证成功后跳到这里，带 {email, message:'验证成功，请登录'}；
  // R7：注册页链过来的 notice（匿名任务认领提示）也从 state 里接住，登录后优先用
  // 登录接口自己的 notice，没有再用这个兜底
  const locState = (location.state as { email?: string; message?: string; notice?: string | null } | null);
  const flash = locState?.message ?? null;
  const initialEmail = locState?.email ?? '';
  const chainedNotice = locState?.notice ?? null;
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState(initialEmail);
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unverified, setUnverified] = useState(false);
  // R8-U-2：管理员账号触发 totp_required 时给后台登录入口
  const [totpRequired, setTotpRequired] = useState(false);
  const [resending, setResending] = useState(false);

  const submit = async () => {
    setError(null);
    setUnverified(false);
    setTotpRequired(false);
    if (!email.trim() || !password) {
      if (!email.trim() && !password) setError('请填写邮箱和密码');
      else if (!email.trim()) setError('请填写邮箱');
      else setError('请填写密码');
      return;
    }
    // R10-P2-4：注册加前端密码强度预校验（后端要求 8 位+字母数字），别等后端 422 英文直出；
    // 登录不预校验（老密码可能短于 8 位，交给后端判）
    if (mode === 'register') {
      if (password.length < 8) {
        setError('密码至少 8 位');
        return;
      }
      // P1：与后端 _validate_password_strength 对齐——必须含字母+数字
      if (!/[a-zA-Z]/.test(password) || !/\d/.test(password)) {
        setError('密码必须包含字母和数字');
        return;
      }
    }
    setBusy(true);
    try {
      if (mode === 'login') {
        const res = await api.login(email.trim(), password);
        // R6-I12：totp_required 时不自动跳首页——后端只建了半会话，直接进首页会被 403 踢回；
        // 管理员账号请前往后台完成 TOTP 验证
        if (res.totp_required === true) {
          setError('管理员请前往后台登录完成 TOTP 验证');
          setTotpRequired(true);
          return;
        }
        // R7：登录/注册认领了匿名 device 任务时后端返回 notice 文案，
        // 经 location.state 带到首页展示一次（Home 读完即清，不重复弹）。
        // 不用 window.location.href 全页刷新——refreshMe 刷新身份即可
        await refreshMe().catch(() => {
          /* /me 失败则首页按匿名态渲染，由 App 的任务错误态兜底 */
        });
        navigate('/', { state: { notice: res.notice ?? chainedNotice ?? null } });
      } else {
        const res = await api.register(email.trim(), password);
        // 注册成功 → 跳邮箱验证页（6 位验证码）；认领提示先带给验证页，
        // 验证成功后链到登录页，最终由登录成功统一带到首页；
        // R9-I15：email_sent 透传给验证页（SMTP 瞬断首发失败时前端明确提示重发）
        navigate('/verify', {
          state: { email: email.trim(), notice: res.notice ?? null, emailSent: res.email_sent ?? true },
          replace: true,
        });
      }
    } catch (e) {
      if (e instanceof ApiError && e.code === 'email_unverified') {
        setUnverified(true);
        setError('请先验证邮箱');
      } else {
        setError(e instanceof Error ? e.message : '操作失败');
      }
    } finally {
      setBusy(false);
    }
  };

  const resend = async () => {
    setResending(true);
    setError(null);
    try {
      await api.resendCode(email.trim());
      navigate('/verify', { state: { email: email.trim() } });
    } catch (e) {
      // 后端 POST /auth/resend-code 已实现（契约回归通过），404 分支为死代码已删除
      setError(e instanceof Error ? e.message : '重发失败');
    } finally {
      setResending(false);
    }
  };

  return (
    <div>
      <PageHeader title="监控" subtitle="Apple 直营店库存监控" />
      <div className="px-6">
        {flash && (
          <p className="mb-4 text-sm text-ok text-center font-medium">{flash}</p>
        )}
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
              // R10-P2-5：邮箱框回车也提交（以前只有密码框绑了 Enter）
              onKeyDown={(e) => e.key === 'Enter' && submit()}
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
          {/* R8-U-2：totp_required 时给"前往后台登录"按钮（后台是独立应用，/admin 不在前端路由里，
              用整页跳转而不是 react-router Link，避免被 catch-all 劫持到首页） */}
          {totpRequired && (
            <div className="mt-4 text-center">
              <a
                href="/admin"
                className="inline-block px-8 py-2.5 rounded-card-sm bg-island text-white text-sm font-medium active:scale-[0.98] transition"
              >
                前往后台登录
              </a>
            </div>
          )}
          {unverified && (
            <div className="mt-4 rounded-card-sm bg-bg p-4 text-center">
              <p className="text-sm text-sub">该邮箱尚未验证，验证后才能登录。</p>
              <div className="mt-3 flex gap-2">
                <button
                  onClick={resend}
                  disabled={resending || !email.trim()}
                  className="flex-1 py-2.5 rounded-card-sm bg-island text-white text-sm font-medium active:scale-[0.98] transition disabled:opacity-40"
                >
                  {resending ? '发送中…' : '重新发送验证码'}
                </button>
                <button
                  onClick={() => navigate('/verify', { state: { email: email.trim() } })}
                  className="flex-1 py-2.5 rounded-card-sm bg-white text-sm font-medium text-accent shadow-card active:scale-[0.98] transition"
                >
                  去验证
                </button>
              </div>
            </div>
          )}
          <div className="mt-5">
            <PrimaryButton onClick={submit} disabled={busy}>
              {busy ? '请稍候…' : mode === 'login' ? '登录' : '注册并验证邮箱'}
            </PrimaryButton>
          </div>
        </Card>
        <p className="mt-4 text-center text-xs text-faint">
          注册即开通免费版（匿名体验的任务会自动迁移过来）
        </p>
      </div>
    </div>
  );
}
