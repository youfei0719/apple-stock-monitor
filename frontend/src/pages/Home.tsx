import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, summarizeTask, type Quota, type Task, type TaskStatusFilter } from '../lib/api';
import { useApp } from '../components/App';
import StockStateBadge from '../components/StockStateBadge';
import { Card, EmptyState, ErrorState, LoadingState, PageHeader } from '../components/ui';

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function fmtHM(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

function daysLeft(iso: string | null): number | null {
  if (!iso) return null;
  return Math.ceil((new Date(iso).getTime() - Date.now()) / 86400000);
}

function TaskCard({
  task,
  onChanged,
  trialExhausted,
}: {
  task: Task;
  onChanged: () => void;
  trialExhausted: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const { state, availableCount, total, updatedAt, partialUnknown } = summarizeTask(task);
  const expired = state === 'expired';
  const remaining = daysLeft(task.expires_at);
  const expiringSoon = !expired && remaining !== null && remaining <= 3;

  const togglePause = async () => {
    setBusy(true);
    try {
      await api.updateTask(task.id, { paused: !task.paused });
      await onChanged();
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!window.confirm(`删除监控任务「${task.name}」？`)) return;
    setBusy(true);
    try {
      await api.deleteTask(task.id);
      await onChanged();
    } finally {
      setBusy(false);
    }
  };

  const renew = async () => {
    setBusy(true);
    try {
      await api.renewTask(task.id);
      await onChanged();
    } catch (e) {
      alert(e instanceof Error ? e.message : '续期失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="p-4 rise-in">
      <Link to={`/tasks/${task.id}`} className="block">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-medium truncate">{task.name}</p>
            <p className="mt-1 text-xs text-sub">
              <span className="product-name">{task.product_name}</span>
              {task.capacity && <> · {task.capacity}</>}
              {task.color && <> · {task.color}</>}
            </p>
            <p className="mt-0.5 text-xs text-faint">
              <span className="mono">{task.part_number}</span>
              {' · '}
              {task.stores.length} 家门店
              {remaining !== null && !expired && (
                <span className={expiringSoon ? ' text-bad font-medium' : ''}>
                  {' · '}剩余 <span className="mono">{remaining}</span> 天
                </span>
              )}
            </p>
          </div>
          <div className="flex flex-col items-end gap-1.5 shrink-0">
            <StockStateBadge state={state} />
            {partialUnknown && (
              <span className="inline-flex items-center gap-1.5 rounded-pill border border-dashed border-[#f0c36d] bg-[#fff7e8] px-2 py-0.5 text-[11px] font-medium text-[#b25e09]">
                <span className="w-1.5 h-1.5 rounded-full bg-warn" />
                部分未知
              </span>
            )}
          </div>
        </div>
        {(total > 0 || updatedAt) && (
          <div className="mt-3 flex items-center justify-between text-xs text-sub">
            <span>
              有货 <span className="mono text-ink">{availableCount}</span>
              {' / '}
              <span className="mono">{total}</span> 个组合
            </span>
            {updatedAt && (
              <span>
                更新于 <span className="mono">{fmtTime(updatedAt)}</span>
              </span>
            )}
          </div>
        )}
        {state === 'cooling' && (
          <p className="mt-1.5 text-[11px] text-warn">
            数据可能过期
            {updatedAt && (
              <>
                {' '}· 更新于 <span className="mono">{fmtHM(updatedAt)}</span>
              </>
            )}
          </p>
        )}
        {task.paused && !expired && (
          <p className="mt-1.5 text-[11px] text-faint">暂停仍占用任务名额</p>
        )}
        {trialExhausted && (
          <p className="mt-1.5 text-[11px] text-bad font-medium">
            体验推送已用完，去<Link to="/me" className="underline">注册 / 升级</Link>继续监控
          </p>
        )}
      </Link>
      <div className="mt-3 pt-3 border-t border-line flex gap-2">
        {expired ? (
          <button
            onClick={renew}
            disabled={busy}
            className="flex-1 py-2 rounded-card-sm bg-accent text-white text-[13px] font-medium active:scale-[0.98] transition disabled:opacity-40"
          >
            {busy ? '续期中…' : '一键续期（+30 天）'}
          </button>
        ) : (
          <button
            onClick={togglePause}
            disabled={busy}
            className="flex-1 py-2 rounded-card-sm bg-bg text-[13px] font-medium active:scale-[0.98] transition disabled:opacity-40"
          >
            {task.paused ? '恢复监控' : '暂停'}
          </button>
        )}
        <button
          onClick={remove}
          disabled={busy}
          className="flex-1 py-2 rounded-card-sm bg-bg text-[13px] font-medium text-bad active:scale-[0.98] transition disabled:opacity-40"
        >
          删除
        </button>
      </div>
    </Card>
  );
}

const TABS: { id: TaskStatusFilter; label: string }[] = [
  { id: 'active', label: '监控中' },
  { id: 'expired', label: '已过期' },
];

export default function Home() {
  const { me, tasks, refreshTasks } = useApp();
  const [tab, setTab] = useState<TaskStatusFilter>('active');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quota, setQuota] = useState<Quota | null>(null);

  const reload = async (t: TaskStatusFilter = tab) => {
    setLoading(true);
    setError(null);
    try {
      await refreshTasks(t);
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    reload(tab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  // 体验版配额耗尽 → 任务卡提示"体验推送已用完，去注册/升级"（断裂-9）
  useEffect(() => {
    api
      .quota()
      .then(setQuota)
      .catch(() => setQuota(null));
  }, []);
  const trialExhausted =
    // 体验档判定走有效档位（/quota 的 tier），不过期的 /me 原始 tier（N2）
    (quota?.tier ?? me?.tier) === 'trial' &&
    quota !== null &&
    quota.push_limit > 0 &&
    quota.push_used >= quota.push_limit;

  return (
    <div>
      <PageHeader title="DING" subtitle="Apple 直营店自提库存监控" />
      <div className="px-4 pb-4">
        <div className="flex gap-2 mb-4">
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
        {error && <ErrorState message={error} onRetry={() => reload()} />}
        {!error && loading && tasks.length === 0 && <LoadingState rows={3} />}
        {!error && !loading && tasks.length === 0 && (
          <EmptyState
            title={tab === 'expired' ? '没有已过期的任务' : '还没有监控任务'}
            hint={tab === 'expired' ? '过期的任务会出现在这里，可一键续期' : '添加你想抢的机型和门店，有货立刻 DING 你'}
            action={
              tab === 'active' ? (
                <Link
                  to="/add"
                  className="inline-block px-8 py-3 rounded-pill bg-accent text-white text-sm font-medium active:scale-95 transition"
                >
                  添加监控
                </Link>
              ) : null
            }
          />
        )}
        {!error && tasks.length > 0 && (
          <div className="space-y-3">
            {tasks.map((t) => (
              <TaskCard
                key={t.id}
                task={t}
                onChanged={() => reload()}
                trialExhausted={trialExhausted}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
