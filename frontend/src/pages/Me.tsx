import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, fmtCny, type ChannelHealth, type Payment, type Plan, type Quota } from '../lib/api';
import { useApp } from '../components/App';
import { NotificationHistory } from '../components/NotifyChannels';
import { Card, EmptyState, ErrorState, LoadingState, PageHeader, PrimaryButton } from '../components/ui';

const TIER_LABEL: Record<string, string> = {
  trial: '免费',
  free: '免费',
  standard: '标准',
  pro: 'Pro',
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
  pro: 'Pro版',
  trial: '免费版',
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
    `推送渠道：${channels}`,
    `监控任务 ${p.tasks_limit} 个`,
    `每周期推送 ${p.push_limit} 次`,
    `刷新间隔 ${p.refresh_interval_sec} 秒`,
  ];
  if (p.history) feats.push('完整历史数据');
  if (p.priority) feats.push('高峰期优先查询');
  return feats;
}

function planPeriodLabel(p: Plan): string {
  if (p.price_cny === 0) return '免费';
  return `¥${p.price_cny} / 30天`;
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

function QuotaBar({ label, used, limit }: { label: string; used: number; limit: number }) {
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
  const warn = pct >= 80 && pct < 100;
  const full = pct >= 100;
  return (
    <div>
      <div className="flex items-center justify-between text-sm mb-1.5">
        {/* P2：配额卡是深色底（bg-island），不用浅色底的 text-sub/text-faint，
            否则标签与数字对比度崩掉近乎隐形 */}
        <span className="text-white/70">
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
        <span className="mono text-white">
          {used}
          <span className="text-white/50"> / {limit}</span>
        </span>
      </div>
      <div className="h-2 rounded-full bg-white/15 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${full ? 'bg-bad' : warn ? 'bg-warn' : 'bg-accent'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function ChannelHealthCard() {
  const [channels, setChannels] = useState<ChannelHealth[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .channelHealth()
      .then((r) => setChannels(r.channels))
      .catch((e) => {
        setChannels([]);
        setError(e instanceof Error ? e.message : '加载失败');
      });
  }, []);

  return (
    <section>
      <h2 className="text-[13px] font-semibold text-sub mb-2">通道健康</h2>
      <Card className="p-4 rise-in">
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
                      {c.success_rate_7d == null ? '暂无数据' : `${(c.success_rate_7d * 100).toFixed(0)}%`}
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
          通道挂掉时请检查配置或换通道重试；通知历史里可查看每次发送的失败原因。
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
            <input
              type="password"
              value={oldPw}
              onChange={(e) => setOldPw(e.target.value)}
              placeholder="当前密码"
              autoComplete="current-password"
              className="w-full px-3 py-2.5 rounded-card-sm bg-bg text-sm outline-none placeholder:text-faint"
            />
            <input
              type="password"
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              placeholder="新密码"
              autoComplete="new-password"
              className="w-full px-3 py-2.5 rounded-card-sm bg-bg text-sm outline-none placeholder:text-faint"
            />
            <input
              type="password"
              value={confirmPw}
              onChange={(e) => setConfirmPw(e.target.value)}
              placeholder="再次输入新密码"
              autoComplete="new-password"
              className="w-full px-3 py-2.5 rounded-card-sm bg-bg text-sm outline-none placeholder:text-faint"
            />
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
  const { me, logout } = useApp();
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
              注册 / 登录后可管理会员档位、查看配额与付费记录，匿名创建的任务会自动迁移过来。
            </p>
            <Link
              to="/login"
              className="mt-5 inline-block px-6 py-2.5 rounded-pill bg-accent text-white text-sm font-medium active:scale-95 transition"
            >
              去注册 / 登录
            </Link>
          </Card>
          {/* UX：匿名态也展示两档价格——注册前就能了解付费信息，不错过转化 */}
          <section>
            <h2 className="text-[13px] font-semibold text-sub mb-2">会员档位</h2>
            {plansErr && <ErrorState message={plansErr} onRetry={load} />}
            {!plansErr && plans === null && <LoadingState rows={1} />}
            {!plansErr && plans !== null && plans.length > 0 && (
              <div className="grid grid-cols-2 gap-2.5">
                {plans.map((p) => (
                  <Card key={p.tier} className="p-4 rise-in">
                    <p className="font-semibold">{p.name}</p>
                    <p className="mt-2 mono text-xl font-semibold">{planPeriodLabel(p)}</p>
                    <ul className="mt-2 space-y-1">
                      {planFeatures(p)
                        .slice(0, 3)
                        .map((f) => (
                          <li key={f} className="text-xs text-sub">
                            · {f}
                          </li>
                        ))}
                    </ul>
                  </Card>
                ))}
              </div>
            )}
            <p className="mt-2 text-[11px] text-faint text-center">
              注册后通过爱发电赞助开通，支付成功自动开通对应档位
            </p>
          </section>
        </div>
      </div>
    );
  }

  // N2：展示档位统一用 /quota 的有效档位（quota.tier，过期按 free 算）；
  // /me 已由后端返回有效档位（auth.py:425 effective_tier，过期按 free 算），这里只做兜底
  const displayTier = quota?.tier ?? me.tier;
  const tierLabel = `${TIER_LABEL[displayTier] ?? displayTier}版`;
  const expiry = quota ? fmtBeijingDate(quota.tier_expires_at) : null;

  return (
    <div>
      <PageHeader title="我的" subtitle={me.email} />
      <div className="px-4 pb-6 space-y-5">
        {/* 当前会员 */}
        {/* P0：Card 自带 bg-white，与 bg-island 冲突导致白底白字；这里不用 Card，直接写 div */}
        <div className="p-5 rise-in bg-island text-white rounded-card shadow-card">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs text-white/60">当前会员</p>
              <p className="mt-1 text-xl font-semibold">
                {tierLabel}
                {expiry && (
                  <span className="ml-2 text-sm font-normal text-white/70">
                    · 到期于 {expiry}（北京时间）
                  </span>
                )}
              </p>
              {/* F-N3：后端 /quota 补 pending_tier（降级预约），防御式渲染"到期后切换" */}
              {quota?.pending_tier && (
                <p className="mt-1 text-[13px] text-white/75">
                  到期后切换为{PAID_TIER_LABEL[quota.pending_tier] ?? quota.pending_tier}
                </p>
              )}
            </div>
            <span className="mono text-xs text-white/60">#{String(me.id)}</span>
          </div>
          {quota && (
            <div className="mt-4 space-y-3">
              <QuotaBar label="推送配额" used={quota.push_used} limit={quota.push_limit} />
              <QuotaBar label="监控任务" used={quota.tasks_used} limit={quota.tasks_limit} />
              <p className="text-xs text-white/60">
                刷新间隔 <span className="mono text-white">{quota.refresh_interval_sec}s</span>
                {' · '}
                {/* F-N2：quota_period_key 实际是下次重置日（购买日 +30 天锚点），不是"周期起始" */}
                下次重置{' '}
                <span className="mono text-white">
                  {fmtBeijingDate(quota.quota_reset_at) ?? quota.period}
                </span>
              </p>
              <p className="text-[11px] text-white/50">
                配额按实际发送成功的通知条数扣减 · 付费档以购买日 +30
                天为一周期滚动重置，匿名体验档按自然月重置
              </p>
            </div>
          )}
          {/* R9-I9：配额接口失败不再静默消失——独立错误态 + 重试 */}
          {!quota &&
            (quotaErr ? (
              <p className="mt-4 text-xs text-white/70">
                配额加载失败：{quotaErr}{' '}
                <button onClick={load} className="underline font-medium">
                  重试
                </button>
              </p>
            ) : (
              <p className="mt-4 text-xs text-white/60">配额加载中…</p>
            ))}
        </div>

        {/* 通道健康 */}
        <ChannelHealthCard />

        {/* 通知历史（标题由 NotificationHistory 组件渲染，这里不重复） */}
        <section>
          <NotificationHistory />
        </section>

        {/* 会员档位 */}
        <section>
          <h2 className="text-[13px] font-semibold text-sub mb-2">会员档位</h2>
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
                  <p className="font-semibold">免费</p>
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
                  免费版由管理员开通，仅支持站内通知（在本站内查看）
                </p>
              </Card>
            </div>
          )}
          {!plansErr && plans !== null && plans.length > 0 && (
            <div className="grid grid-cols-2 gap-2.5">
              {plans.map((p) => {
                const current = p.tier === displayTier;
                return (
                  <Card
                    key={p.tier}
                    className={`p-4 rise-in ${current ? 'ring-2 ring-accent' : ''}`}
                  >
                    <div className="flex items-center justify-between">
                      <p className="font-semibold">{p.name}</p>
                      {current && (
                        <span className="text-[11px] px-2 py-0.5 rounded-pill bg-accent text-white">
                          当前
                        </span>
                      )}
                    </div>
                    <p className="mt-2 mono text-xl font-semibold">{planPeriodLabel(p)}</p>
                    <ul className="mt-2 space-y-1">
                      {planFeatures(p)
                        .slice(0, 4)
                        .map((f) => (
                          <li key={f} className="text-xs text-sub">
                            · {f}
                          </li>
                        ))}
                    </ul>
                  </Card>
                );
              })}
            </div>
          )}
        </section>

        {/* 爱发电开通 */}
        <Card className="p-5 rise-in">
          <p className="font-medium">开通 / 续费会员</p>
          <p className="mt-1.5 text-sm text-sub leading-relaxed">
            通过爱发电赞助开通，支付成功后系统自动开通对应档位（标准 ¥9.9/30天 · Pro ¥19.9/30天）。
          </p>
          <div className="mt-4">
            <PrimaryButton onClick={() => window.open(afdianUrl, '_blank', 'noopener')}>
              前往爱发电开通
            </PrimaryButton>
          </div>
          <p className="mt-2 text-xs text-faint text-center">
            赞助时在备注 / 留言中填写你的用户 ID #{me.id}{' '}或注册邮箱，以便自动开通
          </p>
        </Card>

        {/* 付费记录 */}
        <section>
          <h2 className="text-[13px] font-semibold text-sub mb-2">付费记录</h2>
          {paymentsErr ? (
            // R8-I-11：付费记录接口失败独立报错，不连带吃掉配额/档位展示
            <ErrorState message={paymentsErr} onRetry={load} />
          ) : !payments ? (
            <LoadingState rows={1} />
          ) : payments.length === 0 ? (
            <EmptyState title="暂无付费记录" action={null} />
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
          <p className="mt-2 text-[11px] text-faint leading-relaxed">
            付款通过爱发电完成；如需退款，请在爱发电的赞助订单中申请。退款成功后请联系客服处理，管理员确认后收回档位。
          </p>
        </section>

        {/* R8-I-15 / R18-P1-1：账号安全——修改密码（后端 PATCH /api/auth/me，body: old_password + password） */}
        <ChangePasswordCard />

        <button
          onClick={onLogout}
          className="w-full py-3 rounded-card-sm bg-white text-bad text-[15px] font-medium shadow-card active:scale-[0.98] transition"
        >
          退出登录
        </button>
      </div>
    </div>
  );
}
