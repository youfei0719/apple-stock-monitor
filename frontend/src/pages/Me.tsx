import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, fmtCny, type ChannelHealth, type Payment, type Plan, type Quota } from '../lib/api';
import { useApp } from '../components/App';
import { NotificationHistory } from '../components/NotifyChannels';
import { Card, EmptyState, ErrorState, LoadingState, PageHeader } from '../components/ui';

const TIER_LABEL: Record<string, string> = {
  trial: '体验版',
  free: '免费版',
  standard: '标准版',
  pro: 'Pro 版',
};

const CHANNEL_LABEL: Record<string, string> = {
  page: '站内',
  system: '站内',
  email: '邮件',
  sms: '短信',
  bark: 'Bark',
  wecom: '企业微信',
  dingtalk: '钉钉',
  feishu: '飞书',
};

/** 付费记录档位中文名（N20：按 tier_to 映射，不显示裸英文） */
const PAID_TIER_LABEL: Record<string, string> = {
  standard: '标准版',
  pro: 'Pro 版',
  trial: '体验版',
  free: '免费版',
};

/** 付费记录状态中文映射（后端 Payment.status：paid/amount_mismatch/refunded/cancelled/resolved/unknown_plan） */
const PAY_STATUS_LABEL: Record<string, string> = {
  paid: '已到账',
  amount_mismatch: '金额异常',
  refunded: '已退款',
  resolved: '已处理',
  cancelled: '已取消',
  unknown_plan: '未知档位（待处理）',
};
const PAY_STATUS_CLS: Record<string, string> = {
  paid: 'bg-[#e8f7ec] text-[#1a7f37]',
  amount_mismatch: 'bg-[#fdecea] text-[#b3261e]',
  refunded: 'bg-[#e8f1fd] text-accent',
  resolved: 'bg-[#f2f2f4] text-sub',
  cancelled: 'bg-[#f2f2f4] text-faint',
  unknown_plan: 'bg-[#fff7e8] text-[#b25e09]',
};

/** 按后端 GET /plans 实际字段（tier/name/price_cny/tasks_limit/push_limit/channels/history/priority/refresh_interval_sec）渲染 */
function planFeatures(p: Plan): string[] {
  const channels = p.channels.map((c) => CHANNEL_LABEL[c] ?? c).join('、');
  const feats = [
    `通知方式：${channels}`,
    `监控任务 ${p.tasks_limit} 个`,
    `每 30 天 ${p.push_limit} 次提醒`,
    `检查间隔 ${p.refresh_interval_sec} 秒`,
  ];
  if (p.history) feats.push('通知汇总');
  if (p.priority) feats.push('高峰期优先查询');
  return feats;
}

function planPeriodLabel(p: Plan): string {
  if (p.price_cny === 0) return '¥0';
  return `¥${p.price_cny} / 30 天`;
}

/** 北京时间格式化（配额按购买日 +30 天滚动，展示时标注北京时间） */
function fmtBeijingDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  return new Date(iso).toLocaleDateString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
}

function QuotaBar({ label, used, limit, dark }: { label: string; used: number; limit: number; dark?: boolean }) {
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
  const warn = pct >= 80 && pct < 100;
  const full = pct >= 100;
  const labelCls = dark ? 'text-white/70' : 'text-sub';
  const numCls = dark ? 'text-white' : 'text-ink';
  const numDimCls = dark ? 'text-white/50' : 'text-faint';
  const trackCls = dark ? 'bg-white/15' : 'bg-black/10';
  return (
    <div>
      <div className="flex items-center justify-between text-sm mb-1.5">
        <span className={labelCls}>
          {label}
          {full && (
            <span className="ml-2 text-[11px] px-1.5 py-0.5 rounded-pill bg-bad/20 text-bad font-medium">
              已用完
            </span>
          )}
          {warn && (
            <span className="ml-2 text-[11px] px-1.5 py-0.5 rounded-pill bg-warn/20 text-warn font-medium">
              即将用完
            </span>
          )}
        </span>
        <span className={`mono ${numCls}`}>
          {used}
          <span className={numDimCls}> / {limit}</span>
        </span>
      </div>
      <div className={`h-2 rounded-full ${trackCls} overflow-hidden`}>
        <div
          className={`h-full rounded-full transition-all ${full ? 'bg-bad' : warn ? 'bg-warn' : 'bg-accent'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function ChannelHealthCard() {
  const { me } = useApp();
  const [channels, setChannels] = useState<ChannelHealth[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .channelHealth()
      .then((r) => setChannels(r.channels.filter((c) => c.key === "email")))
      .catch((e) => {
        setChannels([]);
        setError(e instanceof Error ? e.message : '加载失败');
      });
  }, []);

  return (
    <section>
      <h2 className="text-[13px] font-semibold text-sub mb-2">邮件状态</h2>
      <Card className="p-4 rise-in">
        {me && <p className="mb-3 text-sm text-sub break-all">默认邮箱：{me.email}</p>}
        {error && <p className="text-xs text-bad">{error}</p>}
        {channels === null && <LoadingState rows={2} />}
        {channels !== null && channels.length === 0 && !error && (
          <EmptyState title="暂无通道数据" action={null} />
        )}
        {channels !== null && channels.length > 0 && (
          <div className="space-y-3">
            {channels.map((c) => (
              <div key={c.key} className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium flex items-center gap-2">
                    {CHANNEL_LABEL[c.key] ?? c.name ?? c.key}
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded-pill font-medium ${
                        c.configured ? 'bg-ok/10 text-ok' : 'bg-bg text-faint'
                      }`}
                    >
                      {c.configured ? '已配置' : '未配置'}
                    </span>
                  </p>
                  <p className="mt-0.5 text-xs text-sub">
                    近 7 天成功率：
                    <span className="mono text-ink">
                      {c.success_rate_7d == null
                        ? '暂无数据'
                        : `${(c.success_rate_7d * 100).toFixed(0)}%（${c.sample_7d} 次）`}
                    </span>
                  </p>
                  {c.last_failure_at && (
                    <p className="mt-0.5 text-xs text-bad">
                      最后失败：
                      {/* R9-I7：全站显式北京时间（与页脚承诺一致） */}
                      {new Date(c.last_failure_at).toLocaleString('zh-CN', {
                        timeZone: 'Asia/Shanghai',
                        month: '2-digit',
                        day: '2-digit',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                      {c.last_failure_reason ? ` · ${c.last_failure_reason}` : ''}
                    </p>
                  )}
                </div>
                {c.configured && c.success_rate_7d != null && c.success_rate_7d < 1 && (
                  <span className="shrink-0 w-2 h-2 mt-1.5 rounded-full bg-warn" title="有失败记录" />
                )}
              </div>
            ))}
          </div>
        )}
        <p className="mt-3 text-[11px] text-faint">
          未收到邮件？到任务详情核对邮箱、发送测试，并检查垃圾邮件。
        </p>
      </Card>
    </section>
  );
}

/** R8-I-15 / R18-P1-1：修改密码卡片——后端 PATCH /api/auth/me（auth.py:475 patch_me）已实现，
 * body: {old_password, password}，成功返回 {ok: true}；
 * 注意：后端没有 PATCH /api/me（me_router 只有 GET 别名），调错路径会 405；
 * 后端会删除该用户其他会话（当前会话保留） */
function ChangePasswordCard() {
  const [open, setOpen] = useState(false);
  const [oldPw, setOldPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [confirmPw, setConfirmPw] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [ok, setOk] = useState<boolean | null>(null);

  const submit = async () => {
    setMsg(null);
    setOk(null);
    if (!oldPw || !newPw || !confirmPw) {
      setMsg('请填写完整');
      setOk(false);
      return;
    }
    if (newPw !== confirmPw) {
      setMsg('两次输入的新密码不一致');
      setOk(false);
      return;
    }
    // R10-P2-4：改密加前端密码长度预校验（后端 min_length=8），别等后端 422 英文直出
    if (newPw.length < 8) {
      setMsg('新密码至少 8 位');
      setOk(false);
      return;
    }
    setBusy(true);
    try {
      // R9-I10：改密走 api.changePassword（统一 req()），不再组件内直调 fetch
      await api.changePassword(oldPw, newPw);
      setMsg('密码已修改，其他设备的登录将被登出');
      setOk(true);
      setOldPw('');
      setNewPw('');
      setConfirmPw('');
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '修改失败');
      setOk(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section>
      <h2 className="text-[13px] font-semibold text-sub mb-2">账号安全</h2>
      <Card className="p-4 rise-in">
        {!open ? (
          <button
            onClick={() => setOpen(true)}
            className="w-full py-2.5 rounded-card-sm bg-bg text-sm font-medium active:scale-[0.98] transition"
          >
            修改密码
          </button>
        ) : (
          <div className="space-y-3">
            <label className="block text-sm">当前密码
            <input
              type="password"
              value={oldPw}
              onChange={(e) => setOldPw(e.target.value)}
              aria-label="当前密码" placeholder="当前密码"
              autoComplete="current-password"
              className="mt-1 w-full px-3 py-2.5 rounded-card-sm bg-bg text-sm outline-none placeholder:text-faint"
            />
            </label>
            <label className="block text-sm">新密码
            <input
              type="password"
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              aria-label="新密码" placeholder="至少 8 位，含字母和数字"
              autoComplete="new-password"
              className="mt-1 w-full px-3 py-2.5 rounded-card-sm bg-bg text-sm outline-none placeholder:text-faint"
            />
            </label>
            <label className="block text-sm">确认新密码
            <input
              type="password"
              value={confirmPw}
              onChange={(e) => setConfirmPw(e.target.value)}
              aria-label="确认新密码" placeholder="再次输入新密码"
              autoComplete="new-password"
              className="mt-1 w-full px-3 py-2.5 rounded-card-sm bg-bg text-sm outline-none placeholder:text-faint"
            />
            </label>
            {msg && (
              <p className={`text-xs text-center ${ok ? 'text-ok' : 'text-bad'}`}>{msg}</p>
            )}
            <div className="flex gap-2">
              <button
                onClick={() => {
                  setOpen(false);
                  setMsg(null);
                  setOk(null);
                }}
                className="flex-1 py-2.5 rounded-card-sm bg-bg text-sm font-medium active:scale-[0.98] transition"
              >
                取消
              </button>
              <button
                onClick={submit}
                disabled={busy}
                className="flex-1 py-2.5 rounded-card-sm bg-island text-white text-sm font-medium active:scale-[0.98] transition disabled:opacity-40"
              >
                {busy ? '提交中…' : '确认修改'}
              </button>
            </div>
          </div>
        )}
      </Card>
    </section>
  );
}

export default function Me() {
  const navigate = useNavigate();
  // R8-B0-4：退出登录走 App 层的 logout（先清 me/tasks state 再调后端），
  // 避免后退回 /me 看到旧用户数据
  const { me, logout, refreshMe } = useApp();
  const [selectedPlan, setSelectedPlan] = useState<Plan | null>(null);
  const confirmRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!selectedPlan) return;
    const previous = document.activeElement as HTMLElement | null;
    const dialog = confirmRef.current;
    const trap = (event: KeyboardEvent) => {
      if (event.key !== 'Tab' || !dialog) return;
      const items = dialog.querySelectorAll<HTMLElement>('button, a[href]');
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    dialog?.querySelector<HTMLElement>('button')?.focus();
    document.addEventListener('keydown', trap);
    return () => { document.removeEventListener('keydown', trap); previous?.focus(); };
  }, [selectedPlan]);
  const [checkingEntitlement, setCheckingEntitlement] = useState(false);
  const [purchaseMessage, setPurchaseMessage] = useState<string | null>(null);
  const copyId = async () => {
    try { await navigator.clipboard.writeText(String(me?.id ?? '')); setPurchaseMessage('用户 ID 已复制，赞助时请填写到备注'); }
    catch { setPurchaseMessage(`无法自动复制，请手动复制用户 ID：${me?.id ?? ''}`); }
  };
  const checkEntitlement = async () => {
    setCheckingEntitlement(true); setPurchaseMessage(null);
    try {
      const [currentQuota, records] = await Promise.all([api.quota(), api.payments(), refreshMe()]);
      setQuota(currentQuota); setPayments(records);
      setPurchaseMessage(`当前会员：${TIER_LABEL[currentQuota.tier] ?? currentQuota.tier}。未生效请稍后重试。`);
    } catch (e) { setPurchaseMessage(e instanceof Error ? e.message : '检查失败，请重试'); }
    finally { setCheckingEntitlement(false); }
  };
  const [plans, setPlans] = useState<Plan[] | null>(null);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [payments, setPayments] = useState<Payment[] | null>(null);
  const [afdianUrl, setAfdianUrl] = useState<string>('https://afdian.com/a/stockmon');
  // R8-I-11：各接口错误态独立——任一接口失败不再吃掉整页
  // （配额/档位不能因付费记录接口抖动而消失）
  const [plansErr, setPlansErr] = useState<string | null>(null);
  const [paymentsErr, setPaymentsErr] = useState<string | null>(null);
  // R9-I9：配额加载失败独立错误态（之前 .catch(() => setQuota(null)) 后静默消失）
  const [quotaErr, setQuotaErr] = useState<string | null>(null);

  const load = async () => {
    setPlansErr(null);
    setPaymentsErr(null);
    setQuotaErr(null);
    // R8-I-11：四个接口独立加载互不阻塞；
    // siteConfig 失败仅影响爱发电链接，配额失败时降级用 /me 的有效档位兜底
    api
      .plans()
      .then((p) => setPlans(p.filter((x) => x.tier !== 'trial')))
      .catch((e) => setPlansErr(e instanceof Error ? e.message : '加载失败'));
    api
      .quota()
      .then(setQuota)
      .catch((e) => {
        setQuota(null);
        setQuotaErr(e instanceof Error ? e.message : '加载失败');
      });
    // R10-死代码3：匿名进"我的"时不调 api.payments()——必吃 401，多一次无用请求
    // （匿名页只渲染注册引导，付费记录根本用不上）
    if (me !== null) {
      api
        .payments()
        .then(setPayments)
        .catch((e) => setPaymentsErr(e instanceof Error ? e.message : '加载失败'));
    }
    api
      .siteConfig()
      .then((cfg) => {
        if (cfg?.afdian_page_url) setAfdianUrl(cfg.afdian_page_url);
      })
      .catch(() => {
        /* 爱发电链接失败时保留默认 */
      });
  };

  useEffect(() => {
    load();
  }, []);

  const onLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  if (!me) {
    return (
      <div>
        <PageHeader title="我的" />
        <div className="px-4 pb-6 space-y-5">
          {/* F4：匿名体验中进入"我的"，引导注册/登录而非报错 */}
          <Card className="p-8 text-center rise-in">
            <p className="text-ink font-medium">你正在匿名体验</p>
            <p className="mt-2 text-sm text-sub">
              登录后，体验任务会转入账号。
            </p>
            <Link
              to="/login"
              className="mt-5 inline-block px-6 py-2.5 rounded-pill bg-accent text-white text-sm font-medium active:scale-95 transition"
            >
              去注册 / 登录
            </Link>
          </Card>
          {/* 未登录不展示定价方案 */}
        </div>
      </div>
    );
  }

  // N2：展示档位统一用 /quota 的有效档位（quota.tier，过期按 free 算）；
  // /me 已由后端返回有效档位（auth.py:425 effective_tier，过期按 free 算），这里只做兜底
  const displayTier = quota?.tier ?? me.tier;
  const tierLabel = TIER_LABEL[displayTier] ?? displayTier;
  const expiry = quota ? fmtBeijingDate(quota.tier_expires_at) : null;
  // 会员卡配色：Pro=深色，其余=浅色
  const isProCard = displayTier === 'pro';
  const cardMuted = isProCard ? 'text-white/80' : 'text-sub';
  const cardFaint = isProCard ? 'text-white/70' : 'text-faint';
  const cardStrong = isProCard ? 'text-white' : 'text-ink';

  return (
    <div>
      <PageHeader title="我的" subtitle={me.email} />
      <div className="px-4 pb-6 space-y-5">
        {/* 当前会员：按等级 mesh 流动渐变 */}
        {/* 免费=银灰 mesh，标准=蓝色 mesh，Pro=金色 mesh；速度统一 10s */}
        <div
          className={`p-5 rise-in rounded-card shadow-card relative overflow-hidden tier-mesh ${
            displayTier === 'pro'
              ? 'tier-mesh-gold text-white'
              : displayTier === 'standard'
                ? 'tier-mesh-blue text-ink'
                : 'tier-mesh-silver text-ink'
          }`}
        >
          {/* Pro 专属：顶部金色流光线 */}
          {displayTier === 'pro' && (
            <div className="absolute top-0 inset-x-0 h-[3px] bg-gradient-to-r from-amber-300 via-yellow-400 to-amber-300 rounded-t-card" />
          )}
          <div className="flex items-center justify-between">
            <div>
              <p className={`text-xs ${cardMuted}`}>当前会员</p>
              <p className="mt-1 text-xl font-semibold">
                {tierLabel}
                {/* Pro 专属金色徽章 */}
                {isProCard && (
                  <span className="ml-2 inline-block px-2 py-0.5 text-[11px] font-bold tracking-wider text-amber-300 border border-amber-300/50 rounded-pill align-middle">
                    PRO
                  </span>
                )}
                {expiry && (
                  <span className={`ml-2 text-sm font-normal ${cardMuted}`}>
                    · 到期于 {expiry}（北京时间）
                  </span>
                )}
              </p>
              {/* F-N3：后端 /quota 补 pending_tier（降级预约），防御式渲染"到期后切换" */}
              {quota?.pending_tier && (
                <p className={`mt-1 text-[13px] ${cardMuted}`}>
                  到期后切换为{PAID_TIER_LABEL[quota.pending_tier] ?? quota.pending_tier}
                </p>
              )}
            </div>
            <button onClick={copyId} aria-label="复制用户 ID" className={`mono text-xs underline ${cardMuted}`}>用户 ID #{String(me.id)} · 复制</button>
          </div>
          {quota && (
            <div className="mt-4 space-y-3">
              <QuotaBar label="提醒额度" used={quota.push_used} limit={quota.push_limit} dark={isProCard} />
              <QuotaBar label="监控任务" used={quota.tasks_used} limit={quota.tasks_limit} dark={isProCard} />
              <div className="mt-1 space-y-1">
                <p className={`text-xs ${cardMuted}`}>
                  检查间隔 <span className={`mono ${cardStrong}`}>{quota.refresh_interval_sec} 秒</span>
                </p>
                <p className={`text-xs ${cardMuted}`}>
                  {/* F-N2：quota_period_key 实际是下次重置日（购买日 +30 天锚点），不是"周期起始" */}
                  额度重置{' '}
                  <span className={`mono ${cardStrong}`}>
                    {fmtBeijingDate(quota.quota_reset_at) ?? quota.period}
                  </span>
                </p>
              </div>
              <p className={`text-[11px] ${cardFaint}`}>
                成功发送 1 条通知占用 1 次额度，每 30 天重置。
              </p>
            </div>
          )}
          {/* R9-I9：配额接口失败不再静默消失——独立错误态 + 重试 */}
          {!quota &&
            (quotaErr ? (
              <p className={`mt-4 text-xs ${cardMuted}`}>
                配额加载失败：{quotaErr}{' '}
                <button onClick={load} className="underline font-medium">
                  重试
                </button>
              </p>
            ) : (
              <p className={`mt-4 text-xs ${cardMuted}`}>配额加载中…</p>
            ))}
        </div>

        {/* 邮件状态 */}
        <ChannelHealthCard />

        {/* 通知历史（标题由 NotificationHistory 组件渲染，这里不重复） */}
        <section>
          <NotificationHistory />
        </section>

        {/* 会员档位 */}
        <section>
          <h2 className="text-[13px] font-semibold text-sub mb-2">会员方案</h2>
          {plansErr && <ErrorState message={plansErr} onRetry={load} />}
          {!plansErr && plans === null && <LoadingState rows={2} />}
          {!plansErr && plans !== null && plans.length === 0 && (
            <EmptyState title="暂无档位信息" action={null} />
          )}
          {/* UI-4：被 admin 授予 trial 的登录用户——/plans 不含 trial，单独渲染体验卡，不直接过滤掉 */}
          {!plansErr && plans !== null && displayTier === 'trial' && (
            <div className="grid grid-cols-2 gap-2.5">
              <Card className="p-4 rise-in ring-2 ring-accent">
                <div className="flex items-center justify-between">
                  <p className="font-semibold">体验</p>
                  <span className="text-[11px] px-2 py-0.5 rounded-pill bg-accent text-white">
                    当前
                  </span>
                </div>
                <p className="mt-2 mono text-xl font-semibold">¥0</p>
                <ul className="mt-2 space-y-1">
                  {planFeatures({
                    tier: 'trial',
                    name: '体验',
                    price_cny: 0,
                    tasks_limit: quota?.tasks_limit ?? 1,
                    push_limit: quota?.push_limit ?? 1,
                    channels: ['page'],
                    history: false,
                    priority: false,
                    refresh_interval_sec: quota?.refresh_interval_sec ?? 300,
                  } as Plan)
                    .slice(0, 4)
                    .map((f) => (
                      <li key={f} className="text-xs text-sub">
                        · {f}
                      </li>
                    ))}
                </ul>
                <p className="mt-2 text-[11px] text-faint">
                  体验版仅显示站内提醒。
                </p>
              </Card>
            </div>
          )}
          {!plansErr && plans !== null && plans.length > 0 && (
            <div className="grid grid-cols-2 gap-2.5">
              {[...plans]
                .sort((a, b) => {
                  // 排序：Pro 最前，标准其次，免费最后
                  const order = { pro: 0, standard: 1, free: 2, trial: 3 };
                  return (order[a.tier] ?? 99) - (order[b.tier] ?? 99);
                })
                .map((p) => {
                const current = p.tier === displayTier;
                const isProPlan = p.tier === 'pro';
                const isStdPlan = p.tier === 'standard';
                const tierMeshCls = isProPlan
                  ? 'tier-mesh tier-mesh-gold text-white'
                  : isStdPlan
                    ? 'tier-mesh tier-mesh-blue text-ink'
                    : 'tier-mesh tier-mesh-silver text-ink';
                const isPaid = isProPlan || isStdPlan;
                const PlanCard = isPaid ? 'a' : 'div';
                return (
                  <PlanCard
                    key={p.tier}
                    href={isPaid ? afdianUrl : undefined}
                    onClick={isPaid ? (e: React.MouseEvent) => { e.preventDefault(); setSelectedPlan(p); } : undefined}
                    target={isPaid ? '_blank' : undefined}
                    rel={isPaid ? 'noopener noreferrer' : undefined}
                    aria-label={isPaid ? `在爱发电开通${TIER_LABEL[p.tier] ?? p.name}` : undefined}
                    className={`block p-4 rise-in relative overflow-hidden rounded-card shadow-card ${
                      current ? 'ring-2 ring-accent' : ''
                    } ${tierMeshCls} ${isPaid ? 'cursor-pointer active:scale-[0.98] transition' : ''}`}
                  >
                    {isProPlan && (
                      <div className="absolute top-0 inset-x-0 h-[2px] bg-gradient-to-r from-amber-300 via-yellow-400 to-amber-300 rounded-t-card" />
                    )}
                    <div className="flex items-center justify-between">
                      <p className="font-semibold">
                        {p.name}
                        {isProPlan && (
                          <span className="ml-1.5 inline-block px-1.5 py-px text-[10px] font-bold tracking-wider text-amber-300 border border-amber-300/50 rounded-pill align-middle">
                            PRO
                          </span>
                        )}
                      </p>
                      {current && (
                        <span className="text-[11px] px-2 py-0.5 rounded-pill bg-accent text-white">
                          当前
                        </span>
                      )}
                    </div>
                    <p className="mt-2 mono text-xl font-semibold">{planPeriodLabel(p)}</p>
                    <ul className="mt-2 space-y-1">
                      {planFeatures(p)
                        .map((f) => (
                          <li key={f} className={`text-xs ${isProPlan ? 'text-white/70' : 'text-sub'}`}>
                            · {f}
                          </li>
                        ))}
                    </ul>
                    {isPaid && !current && (
                      <p className={`mt-2 text-[11px] font-medium ${isProPlan ? 'text-amber-300' : 'text-accent'}`}>
                        点击前往开通 →
                      </p>
                    )}
                  </PlanCard>
                );
              })}
            </div>
          )}
          {/* 爱发电统一赞助页，需在那里选择对应档位并备注用户 ID。 */}
          <p className="mt-2 text-[11px] text-faint text-center">
            付款时需备注用户 ID #{me.id}
          </p>
        </section>

        <Card className="p-4 mb-5">
          <p className="text-sm font-semibold">已付款？</p>
          <p className="mt-1 text-xs text-sub">返回后刷新权益，开通可能需要几分钟。</p>
          <button onClick={checkEntitlement} disabled={checkingEntitlement} className="mt-3 text-sm text-accent disabled:opacity-40">{checkingEntitlement ? '检查中…' : '检查开通结果'}</button>
          {purchaseMessage && <p role="status" className="mt-2 text-sm text-sub">{purchaseMessage}</p>}
        </Card>
        {selectedPlan && <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-5" onKeyDown={(e) => { if (e.key === 'Escape') setSelectedPlan(null); }}>
          <Card className="p-5 w-full max-w-sm">
            <div ref={confirmRef} role="dialog" aria-modal="true" aria-labelledby="plan-confirm-title">
              <h2 id="plan-confirm-title" className="text-lg font-semibold">核对开通信息</h2>
              <p className="mt-3 text-sm">{selectedPlan.name} · {planPeriodLabel(selectedPlan)}</p>
              <p className="mt-2 text-sm">请在爱发电选择该档位，并在备注填写用户 ID：{me.id}。</p>
              <button onClick={copyId} className="mt-3 text-accent text-sm">复制用户 ID</button>
              <div className="flex gap-4 mt-5">
                <button onClick={() => setSelectedPlan(null)} className="text-sm text-sub">取消</button>
                <a href={afdianUrl} target="_blank" rel="noopener noreferrer" onClick={() => setSelectedPlan(null)} className="text-sm text-accent">前往爱发电</a>
              </div>
            </div>
          </Card>
        </div>}
        {/* 付费记录 */}
        <section>
          <h2 className="text-[13px] font-semibold text-sub mb-2">付费记录</h2>
          {paymentsErr ? (
            // R8-I-11：付费记录接口失败独立报错，不连带吃掉配额/档位展示
            <ErrorState message={paymentsErr} onRetry={load} />
          ) : !payments ? (
            <LoadingState rows={1} />
          ) : payments.length === 0 ? (
            // P0：有付费档位但无付费记录（如管理员赠送）时解释清楚，不让用户怀疑付费链路
            quota && quota.tier !== 'free' && quota.tier !== 'trial' ? (
              <Card className="p-4">
                <p className="text-sm text-sub">当前 {TIER_LABEL[quota.tier] ?? quota.tier} 由系统直接开通，无在线支付记录。</p>
              </Card>
            ) : (
              <EmptyState title="暂无付费记录" action={null} />
            )
          ) : (
            <div className="space-y-2">
              {payments.map((p) => (
                <Card key={p.id} className="p-3.5 flex items-center justify-between rise-in">
                  <div>
                    {/* N20：按 tier_to 映射中文档位，不显示裸英文 plan */}
                    <p className="text-sm font-medium">
                      {p.tier_to ? PAID_TIER_LABEL[p.tier_to] ?? p.tier_to : p.plan || '—'}
                      <span
                        className={`ml-2 px-2 py-0.5 rounded-pill text-[11px] font-medium ${
                          PAY_STATUS_CLS[p.status] ?? 'bg-[#f2f2f4] text-sub'
                        }`}
                      >
                        {PAY_STATUS_LABEL[p.status] ?? p.status}
                      </span>
                    </p>
                    <p className="mono text-[11px] text-faint">
                      {/* R9-I7：全站显式北京时间（与页脚承诺一致） */}
                      {new Date(p.created_at).toLocaleDateString('zh-CN', {
                        timeZone: 'Asia/Shanghai',
                      })}
                      {' · '}
                      {p.order_id}
                    </p>
                  </div>
                  <span className="mono text-sm">{fmtCny(p.amount_cny)}</span>
                </Card>
              ))}
            </div>
          )}
          {/* R8-U-9：退款指引——退款走爱发电平台操作，本站不收钱。
              R13-P2-6：对账 job 未落地前不承诺"退款成功后自动收回档位" */}
          <details className="mt-2 text-xs text-sub"><summary className="cursor-pointer">退款说明</summary><p className="mt-2">在爱发电订单中申请退款，成功后请联系客服处理会员权益。</p></details>
        </section>

        {/* R8-I-15 / R18-P1-1：账号安全——修改密码（后端 PATCH /api/auth/me，body: old_password + password） */}
        <ChangePasswordCard />

        <button
          onClick={onLogout}
          className="w-full py-3 rounded-card-sm bg-white text-sub text-[15px] font-medium shadow-card active:scale-[0.98] transition"
        >
          退出登录
        </button>
      </div>
    </div>
  );
}
