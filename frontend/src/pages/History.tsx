import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError, type PollStats, type RankingItem, type ReleaseRecord, type StockEvent } from '../lib/api';
import { useApp } from '../components/App';
import { CHANNEL_LABEL } from '../components/NotifyChannels';
import HistoryCoverage from '../components/HistoryCoverage';
import { Card, EmptyState, ErrorState, LoadingState, PageHeader } from '../components/ui';

const TABS = [
  { id: 'events', label: '通知' },
  { id: 'releases', label: '汇总' },
  { id: 'ranking', label: '城市' },
  { id: 'stats', label: '运行' },
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
            <span className="min-w-0 flex-1 flex gap-2">
              <span className="mono text-faint shrink-0">{String(i + 1).padStart(2, '0')}</span>
              <span className="mono min-w-0 break-words">{label}</span>
            </span>
            <span className="mono shrink-0 ml-3">
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
      <p className="text-sm font-medium mb-1">通知统计 · 近 {days} 天</p>
      <p className="text-xs text-faint mb-3">
        共 <span className="mono text-ink font-semibold">{total}</span> 次到货通知
      </p>
      {byPart.length > 0 && (
        <div className="mb-4">
          <p className="text-[13px] font-medium text-sub mb-2">按机型</p>
          <BarList rows={byPart} unit="次通知" />
        </div>
      )}
      {byDay.length > 0 && (
        <div>
          <p className="text-[13px] font-medium text-sub mb-2">按日期</p>
          <BarList rows={byDay} unit="次通知" />
        </div>
      )}
      {byPart.length === 0 && byDay.length === 0 && (
        <p className="text-sm text-faint">近 {days} 天还没有到货通知</p>
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
  const [refreshKey, setRefreshKey] = useState(0);
  const sequence = useRef(0);
  const identity = useRef('');

  const load = async (t: TabId, force = false) => {
    if (!me) return;
    const current = ++sequence.current;
    setLoading(true);
    setError(null);
    try {
      if (t === 'events' && (!events || force)) {
        const result = await api.events({ days: 30 });
        if (current === sequence.current) setEvents(result);
      }
      if (t === 'releases' && (!releases || force)) {
        try {
          const result = await api.releases(7);
          if (current !== sequence.current) return;
          setReleases(result);
          // R7：刚付完钱回来成功拉到数据 → 同步清除"去开通"升级卡片标记，
          // 否则 releasesForbidden 还卡在 true，付费用户仍看到升级卡
          setReleasesForbidden(false);
        } catch (e) {
          // 无权限（tier_required）→ 渲染升级卡片而非报错页（断裂-18）
          if (current !== sequence.current) return;
          if (e instanceof ApiError && e.code === 'tier_required') {
            setReleasesForbidden(true);
          } else {
            throw e;
          }
        }
      }
      if (t === 'ranking' && (!ranking || force)) {
        const result = await api.ranking(1);
        if (current === sequence.current) setRanking(result);
      }
      if (t === 'stats' && (!stats || force)) {
        const [poll, overview] = await Promise.all([api.pollStats(), api.analyticsOverview()]);
        if (current === sequence.current) setStats({ poll, overview: overview as Record<string, unknown> });
      }
    } catch (e) {
      if (current === sequence.current) setError(e instanceof Error ? e.message : '加载失败');
    } finally {
      if (current === sequence.current) setLoading(false);
    }
  };

  useEffect(() => {
    const key = `${me?.id}:${me?.tier}`;
    const changed = identity.current !== key;
    identity.current = key;
    if (changed) { setEvents(null); setReleases(null); setRanking(null); setStats(null); setReleasesForbidden(false); }
    void load(tab, changed);
    return () => { sequence.current++; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, me?.id, me?.tier]);

  const renderBody = () => {
    // F4：历史接口无匿名链路（后端要求登录），匿名用户引导注册/登录
    if (me === null)
      return (
        <Card className="p-8 text-center rise-in">
          <p className="text-ink font-medium">登录后查看通知记录</p>
          <p className="mt-2 text-sm text-sub">
            登录后，体验任务会转入账号。
          </p>
          <Link
            to="/login" state={{ returnTo: '/history' }}
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
        return <EmptyState title="近 30 天没有到货通知" hint="可在下方查看任务是否正常检查。" action={null} />;
      // 后端 GET /history/events 返回 {id, task_id, part_number, title, body, link, channel, created_at}
      return (
        <div className="space-y-2.5">
          {events.map((e) => (
            <Card key={e.id} className="p-3.5 rise-in">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-[15px] font-medium truncate">{e.title}</p>
                  <p className="mt-0.5 text-xs text-sub whitespace-pre-line">{e.body}</p>
                  <p className="mt-0.5 text-xs text-faint">
                    {/* P1：不裸显 part_number（如 MJYC4CH/A），标题和正文已有完整 SKU 名 */}
                    {CHANNEL_LABEL[e.channel] ?? e.channel}
                    {' · '}
                    {fmt(e.created_at)}
                  </p>
                </div>
                <span className="text-xs text-sub shrink-0">已发送</span>
              </div>
              <div className="mt-2 flex items-center justify-between">
                <span />
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
          <div className="p-6 rounded-card shadow-card rise-in text-center bg-island text-white">
            <p className="text-[15px] font-medium">通知汇总需标准版或 Pro</p>
            <p className="mt-1.5 text-sm text-white/70">
              按日期和机型查看通知次数。
            </p>
            <div className="mt-4">
              <Link
                to="/me"
                className="inline-block px-8 py-3 rounded-pill bg-accent text-white text-sm font-medium active:scale-95 transition"
              >
                去开通
              </Link>
            </div>
          </div>
        );
      if (!releases || releases.length === 0)
        return <EmptyState title="近 7 天没有到货通知" hint="有通知后会显示在这里。" action={null} />;
      // 后端 GET /history/releases 返回 {day, part_number, product_name, events}（按天×机型聚合）
      return (
        <div className="space-y-2.5">
          {releases.map((r, i) => (
            <Card key={i} className="p-3.5 rise-in">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  {/* P1：显示产品名，不裸显 part_number */}
                  <p className="text-[15px] font-medium truncate">{r.product_name || r.part_number}</p>
                  <p className="mt-0.5 text-xs text-sub">{r.day}</p>
                </div>
                <span className="mono text-sm text-ink font-medium shrink-0">{r.events} 次通知</span>
              </div>
            </Card>
          ))}
        </div>
      );
    }

    if (tab === 'ranking') {
      // P1：过滤掉城市为"未知"的数据点，无意义不展示
      const validRanking = (ranking ?? []).filter((r) => r.city && r.city !== '未知');
      if (validRanking.length === 0)
        return (
          <EmptyState
            title="近 24 小时没有到货通知"
            hint="有通知后会按城市统计。"
            action={null}
          />
        );
      // 后端 GET /analytics/ranking 返回 {city, events}（scope=personal：只统计你自己的到货通知）；max 兜底 1 防 NaN
      const max = Math.max(...validRanking.map((r) => r.events), 1);
      return (
        <Card className="p-4 rise-in">
          <p className="text-sm font-medium mb-1">按城市 · 近 24 小时</p>

          <div className="space-y-3">
            {validRanking.map((r, i) => (
              <div key={r.city}>
                <div className="flex items-center justify-between text-sm mb-1">
                  <span>
                    <span className="mono text-faint shrink-0">{String(i + 1).padStart(2, '0')}</span>
                    {r.city}
                  </span>
                  <span className="mono">
                    {r.events}
                    <span className="text-faint text-xs"> 次通知</span>
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
          <p className="text-sm font-medium mb-3">监控运行状态</p>
          <div className="grid grid-cols-2 gap-3">
            {[
              { label: '有检查记录', value: poll ? `${poll.polled_tasks} / ${poll.tasks} 个任务` : '—', mono: true },
              { label: '上次检查时间', value: poll?.last_poll_at ? fmt(poll.last_poll_at) : '—', mono: true },
            ].map((k) => (
              <div key={k.label} className="rounded-card-sm bg-bg p-3">
                <p className="text-xs text-sub">{k.label}</p>
                <p className={`mt-1 text-lg font-semibold ${k.mono ? 'mono' : ''}`}>{k.value}</p>
              </div>
            ))}
          </div>
          <p className="mt-3 text-[11px] text-faint leading-relaxed">
            仅统计各任务最近一次检查，包含失败和旧记录。
          </p>
          {poll?.engine !== null && typeof poll?.engine === 'object' && 'running' in poll.engine && poll.engine.running === false && <p role="status" className="mt-2 text-sm text-bad">检查服务暂未更新，请联系管理员。</p>}
        </Card>
        {overview && Object.keys(overview).length > 0 && (
          <OverviewSummary overview={overview} />
        )}
      </div>
    );
  };

  return (
    <div>
      <PageHeader title="历史" subtitle="通知与检查记录 · 北京时间" />
      <div className="px-4 pb-4">
        <div className="grid grid-cols-4 gap-2 mb-4">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              /* UX：未登录时页签置灰弱化，点击前就明确需要登录，不再点进去才发现看不了 */
              disabled={me === null}
              title={me === null ? '登录后查看' : undefined}
              aria-pressed={tab === t.id}
              className={`px-2 py-2 rounded-pill text-sm font-medium whitespace-nowrap transition active:scale-95 ${
                tab === t.id ? 'bg-island text-white' : 'bg-white text-sub shadow-card'
              } ${me === null ? 'opacity-40' : ''}`}
            >
              {t.label}
            </button>
          ))}
        </div>
        {me !== null && <button onClick={() => { setRefreshKey((value) => value + 1); void load(tab, true); }} disabled={loading} className="mb-3 text-sm text-accent disabled:opacity-40">{loading ? '刷新中…' : '刷新记录'}</button>}
        {me !== null && <details className="mb-3 text-xs text-sub"><summary className="cursor-pointer">记录说明</summary><p className="mt-2">只记录你的已发送通知，重复提醒也计入次数；不代表门店全部库存变化。已发送表示邮件服务已接收。</p></details>}
        {renderBody()}
        {me !== null && <HistoryCoverage refreshKey={refreshKey} />}
      </div>
    </div>
  );
}
