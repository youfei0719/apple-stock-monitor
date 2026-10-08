import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError, type PollStats, type RankingItem, type ReleaseRecord, type StockEvent } from '../lib/api';
import { useApp } from '../components/App';
import { CHANNEL_LABEL } from '../components/NotifyChannels';
import StockStateBadge from '../components/StockStateBadge';
import { Card, EmptyState, ErrorState, LoadingState, PageHeader } from '../components/ui';

const TABS = [
  { id: 'events', label: '活动日志' },
  { id: 'releases', label: '放货记录' },
  { id: 'ranking', label: '我的放货分布' },
  { id: 'stats', label: '数据分析' },
] as const;

type TabId = (typeof TABS)[number]['id'];

// R9-I7：全站显式北京时间（与页脚承诺一致），不走设备本地时区
function fmt(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * 数据分析摘要（F5 修复）。
 * 后端 GET /analytics/overview 契约：{days, total_events, by_part: [[part_number, count]...],
 * by_day: [[day, count]...]}。by_part/by_day 是 [键, 次数] 二元组数组——必须按榜单渲染，
 * 禁止 String() 成逗号文本，也禁止把英文 key（by_part/by_day）裸奔展示。
 */
function BarList({ rows, unit }: { rows: [string, number][]; unit: string }) {
  const max = Math.max(...rows.map(([, n]) => n), 1);
  return (
    <div className="space-y-2.5">
      {rows.map(([label, n], i) => (
        <div key={label}>
          <div className="flex items-center justify-between text-sm mb-1">
            <span className="min-w-0">
              <span className="mono text-faint mr-2">{String(i + 1).padStart(2, '0')}</span>
              <span className="mono truncate">{label}</span>
            </span>
            <span className="mono shrink-0">
              {n}
              <span className="text-faint text-xs"> {unit}</span>
            </span>
          </div>
          <div className="h-2 rounded-full bg-bg overflow-hidden">
            <div
              className="h-full rounded-full bg-accent transition-all"
              style={{ width: `${(n / max) * 100}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

function toPairs(v: unknown): [string, number][] {
  if (!Array.isArray(v)) return [];
  return v
    .filter(
      (p): p is [string, number] =>
        Array.isArray(p) && p.length >= 2 && typeof p[0] === 'string' && typeof p[1] === 'number',
    )
    .map(([k, n]) => [k, n]);
}

function OverviewSummary({ overview }: { overview: Record<string, unknown> }) {
  const days = typeof overview.days === 'number' ? overview.days : 7;
  const total = typeof overview.total_events === 'number' ? overview.total_events : 0;
  const byPart = toPairs(overview.by_part);
  const byDay = toPairs(overview.by_day);
  return (
    <Card className="p-4 rise-in">
      <p className="text-sm font-medium mb-1">数据分析摘要 · 近 {days} 天</p>
      <p className="text-xs text-faint mb-3">
        共 <span className="mono text-ink font-semibold">{total}</span> 次到货通知（仅统计你自己的）
      </p>
      {byPart.length > 0 && (
        <div className="mb-4">
          <p className="text-[13px] font-medium text-sub mb-2">机型放货榜</p>
          <BarList rows={byPart} unit="次放货" />
        </div>
      )}
      {byDay.length > 0 && (
        <div>
          <p className="text-[13px] font-medium text-sub mb-2">每日放货分布</p>
          <BarList rows={byDay} unit="次放货" />
        </div>
      )}
      {byPart.length === 0 && byDay.length === 0 && (
        <p className="text-sm text-faint">近 {days} 天还没有放货记录</p>
      )}
    </Card>
  );
}

export default function History() {
  const { me } = useApp();
  const [tab, setTab] = useState<TabId>('events');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<StockEvent[] | null>(null);
  const [releases, setReleases] = useState<ReleaseRecord[] | null>(null);
  const [releasesForbidden, setReleasesForbidden] = useState(false);
  const [ranking, setRanking] = useState<RankingItem[] | null>(null);
  const [stats, setStats] = useState<{ poll?: PollStats; overview?: Record<string, unknown> } | null>(null);

  const load = async (t: TabId) => {
    setLoading(true);
    setError(null);
    try {
      if (t === 'events' && !events) setEvents(await api.events({ days: 30 }));
      if (t === 'releases' && !releases) {
        try {
          setReleases(await api.releases(7));
          // R7：刚付完钱回来成功拉到数据 → 同步清除"去开通"升级卡片标记，
          // 否则 releasesForbidden 还卡在 true，付费用户仍看到升级卡
          setReleasesForbidden(false);
        } catch (e) {
          // 无权限（tier_required）→ 渲染升级卡片而非报错页（断裂-18）
          if (e instanceof ApiError && e.code === 'tier_required') {
            setReleasesForbidden(true);
          } else {
            throw e;
          }
        }
      }
      if (t === 'ranking' && !ranking) setRanking(await api.ranking(1));
      if (t === 'stats' && !stats) {
        const [poll, overview] = await Promise.all([api.pollStats(), api.analyticsOverview()]);
        setStats({ poll, overview: overview as Record<string, unknown> });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load(tab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  const renderBody = () => {
    // F4：历史接口无匿名链路（后端要求登录），匿名用户引导注册/登录
    if (me === null)
      return (
        <Card className="p-8 text-center rise-in">
          <p className="text-ink font-medium">历史数据需要登录后查看</p>
          <p className="mt-2 text-sm text-sub">
            注册 / 登录后可查看活动日志、放货记录与数据分析，匿名创建的任务会自动迁移过来。
          </p>
          <Link
            to="/login"
            className="mt-5 inline-block px-6 py-2.5 rounded-pill bg-accent text-white text-sm font-medium active:scale-95 transition"
          >
            去注册 / 登录
          </Link>
        </Card>
      );
    if (error) return <ErrorState message={error} onRetry={() => load(tab)} />;
    if (loading) return <LoadingState rows={4} />;

    if (tab === 'events') {
      if (!events || events.length === 0)
        return <EmptyState title="数据积累中" hint="有货事件会出现在这里" action={null} />;
      // 后端 GET /history/events 返回 {id, task_id, part_number, title, body, link, channel, created_at}
      return (
        <div className="space-y-2.5">
          {events.map((e) => (
            <Card key={e.id} className="p-3.5 rise-in">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-[15px] font-medium truncate">{e.title}</p>
                  <p className="mt-0.5 text-xs text-sub">{e.body}</p>
                  <p className="mt-0.5 text-xs text-faint">
                    <span className="mono">{e.part_number}</span>
                    {' · '}
                    {/* R6-I10：复用 NotifyChannels 的 CHANNEL_LABEL 中文映射，不裸显英文 key */}
                    {CHANNEL_LABEL[e.channel] ?? e.channel}
                  </p>
                </div>
                <StockStateBadge state="available" size="sm" />
              </div>
              <div className="mt-2 flex items-center justify-between">
                <span className="mono text-[11px] text-faint">{fmt(e.created_at)}</span>
                {e.link && (
                  <a href={e.link} target="_blank" rel="noopener" className="text-xs text-accent font-medium">
                    去下单 →
                  </a>
                )}
              </div>
            </Card>
          ))}
        </div>
      );
    }

    if (tab === 'releases') {
      if (releasesForbidden)
        return (
          <Card className="p-6 rise-in text-center bg-island text-white">
            <p className="text-[15px] font-medium">完整历史数据是标准版权益</p>
            <p className="mt-1.5 text-sm text-white/70">
              开通标准版后可查看全部放货记录，不再错过每一次放货。
            </p>
            <div className="mt-4">
              <Link
                to="/me"
                className="inline-block px-8 py-3 rounded-pill bg-accent text-white text-sm font-medium active:scale-95 transition"
              >
                去开通
              </Link>
            </div>
          </Card>
        );
      if (!releases || releases.length === 0)
        return <EmptyState title="数据积累中" hint="近 7 天的放货会记录在这里" action={null} />;
      // 后端 GET /history/releases 返回 {day, part_number, events}（按天×机型聚合）
      return (
        <div className="space-y-2.5">
          {releases.map((r, i) => (
            <Card key={i} className="p-3.5 rise-in">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-[15px] font-medium mono truncate">{r.part_number}</p>
                  <p className="mt-0.5 text-xs text-sub">{r.day}</p>
                </div>
                <span className="mono text-sm text-accent shrink-0">{r.events} 次放货</span>
              </div>
            </Card>
          ))}
        </div>
      );
    }

    if (tab === 'ranking') {
      if (!ranking || ranking.length === 0)
        return (
          <EmptyState
            title="数据积累中"
            hint="仅统计你自己的到货通知，今日还没有放货记录"
            action={null}
          />
        );
      // 后端 GET /analytics/ranking 返回 {city, events}（scope=personal：只统计你自己的到货通知）；max 兜底 1 防 NaN
      const max = Math.max(...ranking.map((r) => r.events), 1);
      return (
        <Card className="p-4 rise-in">
          <p className="text-sm font-medium mb-1">我的放货分布 · 今日</p>
          <p className="text-xs text-faint mb-3">仅统计你自己的到货通知</p>
          <div className="space-y-3">
            {ranking.map((r, i) => (
              <div key={r.city}>
                <div className="flex items-center justify-between text-sm mb-1">
                  <span>
                    <span className="mono text-faint mr-2">{String(i + 1).padStart(2, '0')}</span>
                    {r.city}
                  </span>
                  <span className="mono">
                    {r.events}
                    <span className="text-faint text-xs"> 次放货</span>
                  </span>
                </div>
                <div className="h-2 rounded-full bg-bg overflow-hidden">
                  <div
                    className="h-full rounded-full bg-accent transition-all"
                    style={{ width: `${(r.events / max) * 100}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </Card>
      );
    }

    // stats：后端 GET /stats/poll 返回 {tasks, polled_tasks, success_rate, avg_response_ms, last_poll_at, engine}
    if (!stats) return <EmptyState title="数据积累中" action={null} />;
    const poll = stats.poll;
    const overview = stats.overview;
    return (
      <div className="space-y-3">
        <Card className="p-4 rise-in">
          <p className="text-sm font-medium mb-3">查询统计</p>
          <div className="grid grid-cols-2 gap-3">
            {[
              { label: '已轮询任务', value: poll ? String(poll.polled_tasks) : '—', mono: true },
              { label: '成功率', value: poll?.success_rate != null ? `${(poll.success_rate * 100).toFixed(1)}%` : '—', mono: true },
              { label: '平均响应', value: poll?.avg_response_ms != null ? `${Math.round(poll.avg_response_ms)} ms` : '—', mono: true },
              { label: '上次查询', value: poll?.last_poll_at ? fmt(poll.last_poll_at) : '—', mono: true },
            ].map((k) => (
              <div key={k.label} className="rounded-card-sm bg-bg p-3">
                <p className="text-xs text-sub">{k.label}</p>
                <p className={`mt-1 text-lg font-semibold ${k.mono ? 'mono' : ''}`}>{k.value}</p>
              </div>
            ))}
          </div>
        </Card>
        {overview && Object.keys(overview).length > 0 && (
          <OverviewSummary overview={overview} />
        )}
      </div>
    );
  };

  return (
    <div>
      <PageHeader title="历史" subtitle="活动日志 · 放货记录 · 我的放货分布" />
      <div className="px-4 pb-4">
        <div className="flex gap-2 mb-4 overflow-x-auto">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-4 py-2 rounded-pill text-sm font-medium whitespace-nowrap transition active:scale-95 ${
                tab === t.id ? 'bg-island text-white' : 'bg-white text-sub shadow-card'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        {renderBody()}
      </div>
    </div>
  );
}
