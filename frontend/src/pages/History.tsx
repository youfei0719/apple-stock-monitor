import { useEffect, useState } from 'react';
import { api, type PollStats, type RankingItem, type ReleaseRecord, type StockEvent } from '../lib/api';
import StockStateBadge from '../components/StockStateBadge';
import { Card, EmptyState, ErrorState, LoadingState, PageHeader } from '../components/ui';

const TABS = [
  { id: 'events', label: '活动日志' },
  { id: 'releases', label: '放货记录' },
  { id: 'ranking', label: '全国榜单' },
  { id: 'stats', label: '数据分析' },
] as const;

type TabId = (typeof TABS)[number]['id'];

function fmt(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export default function History() {
  const [tab, setTab] = useState<TabId>('events');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<StockEvent[] | null>(null);
  const [releases, setReleases] = useState<ReleaseRecord[] | null>(null);
  const [ranking, setRanking] = useState<RankingItem[] | null>(null);
  const [stats, setStats] = useState<{ poll?: PollStats; overview?: Record<string, unknown> } | null>(null);

  const load = async (t: TabId) => {
    setLoading(true);
    setError(null);
    try {
      if (t === 'events' && !events) setEvents(await api.events({ days: 30 }));
      if (t === 'releases' && !releases) setReleases(await api.releases(7));
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
    if (error) return <ErrorState message={error} onRetry={() => load(tab)} />;
    if (loading) return <LoadingState rows={4} />;

    if (tab === 'events') {
      if (!events || events.length === 0)
        return <EmptyState title="暂无活动日志" hint="有货事件会出现在这里" action={null} />;
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
                    {e.channel}
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
      if (!releases || releases.length === 0)
        return <EmptyState title="暂无放货记录" hint="近 7 天的放货会记录在这里" action={null} />;
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
        return <EmptyState title="暂无榜单数据" hint="今日还没有放货记录" action={null} />;
      // 后端 GET /analytics/ranking 返回 {city, events}；max 兜底 1 防 NaN
      const max = Math.max(...ranking.map((r) => r.events), 1);
      return (
        <Card className="p-4 rise-in">
          <p className="text-sm font-medium mb-3">城市放货排行 · 今日</p>
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
    if (!stats) return <EmptyState title="暂无数据" action={null} />;
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
          <Card className="p-4 rise-in">
            <p className="text-sm font-medium mb-2">数据分析摘要</p>
            <pre className="mono text-xs text-sub whitespace-pre-wrap break-all">
              {JSON.stringify(overview, null, 2)}
            </pre>
          </Card>
        )}
      </div>
    );
  };

  return (
    <div>
      <PageHeader title="历史" subtitle="活动日志 · 放货记录 · 全国榜单" />
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
