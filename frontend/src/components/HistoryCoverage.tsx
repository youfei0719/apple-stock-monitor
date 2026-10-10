import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, isResultStale, isTaskExpired, type Task } from '../lib/api';
import CheckHealth from './CheckHealth';
import { Card, ErrorState, LoadingState } from './ui';

function time(value?: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '尚无记录';
}

export default function HistoryCoverage({ refreshKey }: { refreshKey: number }) {
  const [open, setOpen] = useState(false);
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!open) return;
    let active = true;
    setError(null);
    setTasks(null);
    api.tasks().then((result) => { if (active) setTasks(result); }).catch((e) => {
      if (active) setError(e instanceof Error ? e.message : '检查记录载入失败');
    });
    return () => { active = false; };
  }, [open, refreshKey, retry]);

  return <Card className="p-4 mt-4">
    <button className="text-sm text-accent font-medium w-full text-left" aria-expanded={open} aria-controls="history-coverage" onClick={() => setOpen(!open)}>{open ? '收起检查记录' : '查看任务检查记录'}</button>

    {open && <div id="history-coverage" className="mt-3">
      <p className="text-xs text-sub mb-3">各任务的最新检查记录，不含逐次检查历史。</p>
      {error ? <ErrorState message={error} onRetry={() => setRetry((value) => value + 1)} /> : !tasks ? <LoadingState rows={2} /> : tasks.length === 0 ? <p className="text-sm text-sub">当前没有任务。</p> : <ul className="space-y-4">
        {tasks.map((task) => {
          const stored = Object.entries(task.latest?.stores ?? {}).filter(([number]) => task.stores.some((store) => store.number === number));
          const valid = stored.filter(([, parts]) => Object.values(parts).some((row) => row.updated_at && !isResultStale(task, row.updated_at) && ['available', 'unavailable', 'verifying'].includes(row.state))).length;
          const fresh = task.paused || isTaskExpired(task) || task.last_poll_ok === false ? 0 : valid;
          return <li key={task.id} className="border-t border-line pt-3">
            <Link to={`/tasks/${task.id}`} className="text-sm text-accent font-medium break-words">{task.name}</Link>

            <p className="mt-1 text-xs text-sub">最近检查：{time(task.last_polled_at)}</p>
            <p className="mt-1 text-xs text-sub">有效结果 {fresh} / {task.stores.length} 家门店</p>
            <CheckHealth task={task} />
          </li>;
        })}
      </ul>}
    </div>}
  </Card>;
}
