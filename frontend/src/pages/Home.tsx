import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type Task } from '../lib/api';
import { useApp } from '../components/App';
import StockStateBadge from '../components/StockStateBadge';
import { Card, EmptyState, ErrorState, LoadingState, PageHeader } from '../components/ui';

function TaskCard({ task, onChanged }: { task: Task; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const summary = task.summary;
  const state = task.paused ? 'paused' : (summary?.state ?? 'unknown');

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
            </p>
          </div>
          <StockStateBadge state={state} />
        </div>
        {summary && (
          <div className="mt-3 flex items-center justify-between text-xs text-sub">
            <span>
              有货 <span className="mono text-ink">{summary.available_count}</span>
              {' / '}
              <span className="mono">{summary.total_count}</span> 个组合
            </span>
            <span>
              更新于 <span className="mono">{fmtTime(summary.updated_at)}</span>
            </span>
          </div>
        )}
      </Link>
      <div className="mt-3 pt-3 border-t border-line flex gap-2">
        <button
          onClick={togglePause}
          disabled={busy}
          className="flex-1 py-2 rounded-card-sm bg-bg text-[13px] font-medium active:scale-[0.98] transition disabled:opacity-40"
        >
          {task.paused ? '恢复监控' : '暂停'}
        </button>
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

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export default function Home() {
  const { tasks, refreshTasks } = useApp();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = async () => {
    setLoading(true);
    setError(null);
    try {
      await refreshTasks();
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <PageHeader title="DING" subtitle="Apple 直营店自提库存监控" />
      <div className="px-4 pb-4">
        {error && <ErrorState message={error} onRetry={reload} />}
        {!error && loading && tasks.length === 0 && <LoadingState rows={3} />}
        {!error && !loading && tasks.length === 0 && (
          <EmptyState
            title="还没有监控任务"
            hint="添加你想抢的机型和门店，有货立刻 DING 你"
            action={
              <Link
                to="/add"
                className="inline-block px-8 py-3 rounded-pill bg-accent text-white text-sm font-medium active:scale-95 transition"
              >
                添加监控
              </Link>
            }
          />
        )}
        {!error && tasks.length > 0 && (
          <div className="space-y-3">
            {tasks.map((t) => (
              <TaskCard key={t.id} task={t} onChanged={refreshTasks} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
