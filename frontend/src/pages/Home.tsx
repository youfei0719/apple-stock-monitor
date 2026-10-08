import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
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
  anonymous,
}: {
  task: Task;
  onChanged: () => void;
  trialExhausted: boolean;
  /** F-N5：匿名 trial 任务后端续期钳制到 24h，文案别写死 +30 天 */
  anonymous: boolean;
}) {
  const [busy, setBusy] = useState(false);
  // R6-U3：续期失败用行内错误文案（参考 TaskDetail 的 renewMsg 模式），不再弹原生 alert
  const [renewMsg, setRenewMsg] = useState<string | null>(null);
  // R8-I-16：暂停/恢复失败用行内错误文案，不再 unhandled rejection
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  // R8-U-8：删除确认用应用内弹窗（A·零售式明亮克制风格），不再用 window.confirm
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const { state, availableCount, total, updatedAt, partialUnknown } = summarizeTask(task);
  const expired = state === 'expired';
  const remaining = daysLeft(task.expires_at);
  const expiringSoon = !expired && remaining !== null && remaining <= 3;

  const togglePause = async () => {
    setBusy(true);
    setActionMsg(null);
    try {
      await api.updateTask(task.id, { paused: !task.paused });
      await onChanged();
    } catch (e) {
      // R8-I-16：暂停/恢复失败给行内反馈，不再静默 unhandled rejection
      setActionMsg(e instanceof Error ? e.message : '操作失败');
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await api.deleteTask(task.id);
      await onChanged();
    } catch (e) {
      setActionMsg(e instanceof Error ? e.message : '删除失败');
    } finally {
      setBusy(false);
      setConfirmingDelete(false);
    }
  };

  const renew = async () => {
    setBusy(true);
    setRenewMsg(null);
    try {
      await api.renewTask(task.id);
      await onChanged();
    } catch (e) {
      setRenewMsg(e instanceof Error ? e.message : '续期失败');
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
            体验推送已用完，去
            {/* R6-I11：匿名用户文案"去注册"，已登录用户文案"去升级"
                （被 admin 授予 trial 的已注册用户也会命中横幅） */}
            <Link to={anonymous ? '/login' : '/me'} className="underline">
              {anonymous ? '注册' : '升级'}
            </Link>
            继续监控
          </p>
        )}
      </Link>
      <div className="mt-3 pt-3 border-t border-line">
        {/* R8-I-12：3 天内到期也渲染续期按钮，与详情页规则（expired || days<=3）一致；
            暂停按钮保留在下方第二排，不挤占 */}
        {expiringSoon && (
          <button
            onClick={renew}
            disabled={busy}
            className="w-full mb-2 py-2 rounded-card-sm bg-accent text-white text-[13px] font-medium active:scale-[0.98] transition disabled:opacity-40"
          >
            {busy ? '续期中…' : `一键续期（${anonymous ? '+24 小时' : '+30 天'}）· 剩余 ${remaining} 天`}
          </button>
        )}
        <div className="flex gap-2">
          {expired ? (
            <button
              onClick={renew}
              disabled={busy}
              className="flex-1 py-2 rounded-card-sm bg-accent text-white text-[13px] font-medium active:scale-[0.98] transition disabled:opacity-40"
            >
              {busy ? '续期中…' : `一键续期（${anonymous ? '+24 小时' : '+30 天'}）`}
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
            onClick={() => setConfirmingDelete(true)}
            disabled={busy}
            className="flex-1 py-2 rounded-card-sm bg-bg text-[13px] font-medium text-bad active:scale-[0.98] transition disabled:opacity-40"
          >
            删除
          </button>
        </div>
        {renewMsg && (
          <p className="mt-1.5 text-[11px] text-center text-bad">{renewMsg}</p>
        )}
        {/* R8-I-16：暂停/恢复、删除失败的行内错误文案 */}
        {actionMsg && (
          <p className="mt-1.5 text-[11px] text-center text-bad">{actionMsg}</p>
        )}
      </div>
      {/* R8-U-8：应用内删除确认（底部弹出卡片），替代 window.confirm */}
      {confirmingDelete && (
        <div
          className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 px-4 pb-10"
          onClick={() => !busy && setConfirmingDelete(false)}
        >
          <div
            className="w-full max-w-lg bg-white rounded-card shadow-card-lg p-5 rise-in"
            onClick={(e) => e.stopPropagation()}
          >
            <p className="font-medium">删除监控任务</p>
            <p className="mt-2 text-sm text-sub leading-relaxed">
              确定删除「{task.name}」？删除后不再监控，有货也不会再通知，该操作不可恢复。
            </p>
            <div className="mt-4 flex gap-2">
              <button
                onClick={() => setConfirmingDelete(false)}
                disabled={busy}
                className="flex-1 py-2.5 rounded-card-sm bg-bg text-sm font-medium active:scale-[0.98] transition disabled:opacity-40"
              >
                取消
              </button>
              <button
                onClick={remove}
                disabled={busy}
                className="flex-1 py-2.5 rounded-card-sm bg-bad text-white text-sm font-medium active:scale-[0.98] transition disabled:opacity-40"
              >
                {busy ? '删除中…' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}

const TABS: { id: TaskStatusFilter; label: string }[] = [
  { id: 'active', label: '监控中' },
  { id: 'expired', label: '已过期' },
];

export default function Home() {
  const { me, tasks, refreshTasks, tasksError } = useApp();
  const location = useLocation();
  const navigate = useNavigate();
  const [tab, setTab] = useState<TaskStatusFilter>('active');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quota, setQuota] = useState<Quota | null>(null);
  // R7：登录/注册时认领了匿名 device 任务的提示文案（后端 auth.py _claim_notice，
  // 登录页经 location.state 带过来）。读进本地 state 后立即 replace 掉路由 state，
  // 只展示一次：刷新/后退不再重复弹
  const [claimNotice, setClaimNotice] = useState<string | null>(
    () => (location.state as { notice?: string | null } | null)?.notice ?? null,
  );
  useEffect(() => {
    if ((location.state as { notice?: string | null } | null)?.notice) {
      navigate(location.pathname, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const reload = async (t: TaskStatusFilter = tab) => {
    setLoading(true);
    setError(null);
    try {
      // R6-D5：refreshTasks 会抛异常，这里 catch 后渲染 ErrorState（以前被吞掉永不可达）
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
  // trial 档：仅限未登录匿名体验（后端 tiers.py；匿名推送用量无查询接口，前端无法获知，
  // 匿名页顶有体验横幅代替说明）。登录用户被管理员授予 trial 档时，此横幅仍有效。
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
  // R6-D5：App 层维护的首屏任务加载错误（/me 或 /tasks 失败）也在这里渲染 ErrorState，
  // 而不是误导成"还没有监控任务"
  const loadError = error ?? tasksError;

  return (
    <div>
      <PageHeader title="DING" subtitle="Apple 直营店自提库存监控" />
      <div className="px-4 pb-4">
        {/* R7：匿名任务认领提示横幅（只展示一次，可手动关闭） */}
        {claimNotice && (
          <div className="mb-3 rounded-card-sm bg-white shadow-card px-4 py-3 flex items-start justify-between gap-3 rise-in">
            <p className="text-[13px] text-ink">{claimNotice}</p>
            <button
              onClick={() => setClaimNotice(null)}
              className="shrink-0 text-xs text-faint active:scale-95 transition"
            >
              关闭
            </button>
          </div>
        )}
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
        {loadError && <ErrorState message={loadError} onRetry={() => reload()} />}
        {!loadError && loading && tasks.length === 0 && <LoadingState rows={3} />}
        {!loadError && !loading && tasks.length === 0 && (
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
        {!loadError && tasks.length > 0 && (
          <div className="space-y-3">
            {tasks.map((t) => (
              <TaskCard
                key={t.id}
                task={t}
                onChanged={() => reload()}
                trialExhausted={trialExhausted}
                anonymous={me === null}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
