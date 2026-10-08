import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, type ChannelHealth, type Payment, type Plan, type Quota } from '../lib/api';
import { useApp } from '../components/App';
import { NotificationHistory } from '../components/NotifyChannels';
import { Card, EmptyState, ErrorState, LoadingState, PageHeader, PrimaryButton } from '../components/ui';

const TIER_LABEL: Record<string, string> = {
  trial: '体验',
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
  trial: '体验版',
  free: '免费版',
};

/** 付费记录状态中文映射（后端 Payment.status：paid/amount_mismatch/refunded/cancelled/resolved，未来可能有 unknown_plan） */
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
  return `¥${p.price_cny} / 月`;
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
        <span className="text-sub">
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
        <span className="mono">
          {used}
          <span className="text-faint"> / {limit}</span>
        </span>
      </div>
      <div className="h-2 rounded-full bg-bg overflow-hidden">
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
                    近 7 天成功率{' '}
                    <span className="mono text-ink">
                      {c.success_rate_7d == null ? '暂无数据' : `${(c.success_rate_7d * 100).toFixed(0)}%`}
                    </span>
                  </p>
                  {c.last_failure_at && (
                    <p className="mt-0.5 text-xs text-bad">
                      最后失败：
                      {new Date(c.last_failure_at).toLocaleString('zh-CN', {
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

export default function Me() {
  const navigate = useNavigate();
  const { me } = useApp();
  const [plans, setPlans] = useState<Plan[] | null>(null);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [payments, setPayments] = useState<Payment[] | null>(null);
  const [afdianUrl, setAfdianUrl] = useState<string>('https://afdian.com');
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setError(null);
    try {
      const [p, q, pay, cfg] = await Promise.all([
        api.plans(),
        api.quota(),
        api.payments(),
        api.siteConfig().catch(() => null),
      ]);
      // /plans 不再含 trial；防御性过滤掉体验档
      setPlans(p.filter((x) => x.tier !== 'trial'));
      setQuota(q);
      setPayments(pay);
      if (cfg?.afdian_page_url) setAfdianUrl(cfg.afdian_page_url);
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
    }
  };

  useEffect(() => {
    load();
  }, []);

  const logout = async () => {
    await api.logout();
    navigate('/login', { replace: true });
  };

  if (!me) {
    return (
      <div>
        <PageHeader title="我的" />
        <div className="px-4 pb-6">
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
        </div>
      </div>
    );
  }

  // N2：展示档位统一用 /quota 的有效档位（quota.tier，过期按 free 算）；
  // /me 的原始 tier 在后端未改为有效档位前只做兜底（需后端配合项，见汇报）
  const displayTier = quota?.tier ?? me.tier;
  const tierLabel = `${TIER_LABEL[displayTier] ?? displayTier}版`;
  const expiry = quota ? fmtBeijingDate(quota.tier_expires_at) : null;

  return (
    <div>
      <PageHeader title="我的" subtitle={me.email} />
      <div className="px-4 pb-6 space-y-5">
        {/* 当前会员 */}
        <Card className="p-5 rise-in bg-island text-white">
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
                配额按实际发送成功的通知条数扣减 · 以购买日 +30
                天为一周期滚动重置，体验档同此口径
              </p>
            </div>
          )}
        </Card>

        {/* 通道健康 */}
        <ChannelHealthCard />

        {/* 通知历史 */}
        <section>
          <h2 className="text-[13px] font-semibold text-sub mb-2">通知历史</h2>
          <NotificationHistory />
        </section>

        {/* 会员档位 */}
        <section>
          <h2 className="text-[13px] font-semibold text-sub mb-2">会员档位</h2>
          {error && <ErrorState message={error} onRetry={load} />}
          {!error && plans === null && <LoadingState rows={2} />}
          {!error && plans !== null && plans.length === 0 && (
            <EmptyState title="暂无档位信息" action={null} />
          )}
          {/* UI-4：被 admin 授予 trial 的登录用户——/plans 不含 trial，单独渲染体验卡，不直接过滤掉 */}
          {!error && plans !== null && displayTier === 'trial' && (
            <div className="grid grid-cols-2 gap-2.5">
              <Card className="p-4 rise-in ring-2 ring-accent">
                <div className="flex items-center justify-between">
                  <p className="font-semibold">体验</p>
                  <span className="text-[11px] px-2 py-0.5 rounded-pill bg-accent text-white">
                    当前
                  </span>
                </div>
                <p className="mt-2 mono text-xl font-semibold">免费</p>
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
                  体验版由管理员开通，仅站内通知（在 App 内查看）
                </p>
              </Card>
            </div>
          )}
          {!error && plans !== null && plans.length > 0 && (
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
            通过爱发电赞助开通，支付成功后系统自动开通对应档位（标准 ¥19/月 · Pro ¥39/月）。
          </p>
          <div className="mt-4">
            <PrimaryButton onClick={() => window.open(afdianUrl, '_blank', 'noopener')}>
              前往爱发电开通
            </PrimaryButton>
          </div>
          <p className="mt-2 text-xs text-faint text-center">
            赞助时在备注 / 留言中填写你的用户 ID #{me.id} 或注册邮箱，以便自动开通
          </p>
        </Card>

        {/* 付费记录 */}
        <section>
          <h2 className="text-[13px] font-semibold text-sub mb-2">付费记录</h2>
          {!payments ? (
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
                      {new Date(p.created_at).toLocaleDateString('zh-CN')}
                      {' · '}
                      {p.order_id}
                    </p>
                  </div>
                  <span className="mono text-sm">¥{p.amount_cny}</span>
                </Card>
              ))}
            </div>
          )}
        </section>

        <button
          onClick={logout}
          className="w-full py-3 rounded-card-sm bg-white text-bad text-[15px] font-medium shadow-card active:scale-[0.98] transition"
        >
          退出登录
        </button>
      </div>
    </div>
  );
}
