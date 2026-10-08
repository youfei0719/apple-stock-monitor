import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, type Payment, type Plan, type Quota } from '../lib/api';
import { useApp } from '../components/App';
import { Card, EmptyState, ErrorState, LoadingState, PageHeader, PrimaryButton } from '../components/ui';

const TIER_LABEL: Record<string, string> = {
  trial: '体验',
  free: '免费',
  standard: '标准',
  pro: 'Pro',
};

const CHANNEL_LABEL: Record<string, string> = {
  page: '站内',
  email: '邮件',
  sms: '短信',
  bark: 'Bark',
};

/** 按后端 GET /plans 实际字段（tier/name/price_cny/tasks_limit/push_limit/channels/history/priority/refresh_interval_sec）渲染 */
function planFeatures(p: Plan): string[] {
  const channels = p.channels.map((c) => CHANNEL_LABEL[c] ?? c).join('、');
  const feats = [
    `推送渠道：${channels}`,
    `监控任务 ${p.tasks_limit} 个`,
    `月推送 ${p.push_limit} 次`,
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

function QuotaBar({ label, used, limit }: { label: string; used: number; limit: number }) {
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
  return (
    <div>
      <div className="flex items-center justify-between text-sm mb-1.5">
        <span className="text-sub">{label}</span>
        <span className="mono">
          {used}
          <span className="text-faint"> / {limit}</span>
        </span>
      </div>
      <div className="h-2 rounded-full bg-bg overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${pct >= 90 ? 'bg-bad' : 'bg-accent'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export default function Me() {
  const navigate = useNavigate();
  const { me } = useApp();
  const [plans, setPlans] = useState<Plan[] | null>(null);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [payments, setPayments] = useState<Payment[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setError(null);
    try {
      const [p, q, pay] = await Promise.all([api.plans(), api.quota(), api.payments()]);
      setPlans(p);
      setQuota(q);
      setPayments(pay);
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
                {TIER_LABEL[me.tier] ?? me.tier}
              </p>
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
                {quota.period}
              </p>
            </div>
          )}
        </Card>

        {/* 四档会员 */}
        <section>
          <h2 className="text-[13px] font-semibold text-sub mb-2">会员档位</h2>
          {error && <ErrorState message={error} onRetry={load} />}
          {!error && plans === null && <LoadingState rows={2} />}
          {!error && plans !== null && plans.length === 0 && (
            <EmptyState title="暂无档位信息" action={null} />
          )}
          {!error && plans !== null && plans.length > 0 && (
            <div className="grid grid-cols-2 gap-2.5">
              {plans.map((p) => {
                const current = p.tier === me.tier;
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
            <PrimaryButton onClick={() => window.open('https://afdian.com', '_blank', 'noopener')}>
              前往爱发电开通
            </PrimaryButton>
          </div>
          <p className="mt-2 text-xs text-faint text-center">
            爱发电订单号请与账号邮箱保持一致，以便自动开通
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
                    <p className="text-sm font-medium">{p.plan}</p>
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
