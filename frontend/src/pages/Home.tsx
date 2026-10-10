import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { api, isResultStale, summarizeTask, type Quota, type Task, type TaskStatusFilter } from '../lib/api';
import { useApp } from '../components/App';
import CheckHealth from '../components/CheckHealth';
import StockStateBadge from '../components/StockStateBadge';
import { Card, ConfirmDialog, EmptyState, ErrorState, LoadingState, PageHeader } from '../components/ui';

// R9-I7：全站显式北京时间（与页脚承诺一致），不走设备本地时区
function fmtTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
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
  onRenewNotice,
}: {
  task: Task;
  onChanged: () => void;
  trialExhausted: boolean;
  /** F-N5：匿名 trial 任务后端续期钳制到 24h，文案别写死 +30 天 */
  anonymous: boolean;
  /** R10-P2-3："已过期" tab 续期成功后卡片随 tab 切换卸载，行内 notices 会丢失——
   * 经首页横幅（location.state.notice，AddMonitor 同模式）展示一次 */
  onRenewNotice: (msg: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  // R6-U3：续期失败用行内错误文案（参考 TaskDetail 的 renewMsg 模式），不再弹原生 alert；
  // R9-I14：续期成功时后端 notices（如"已自动恢复 N 个任务"）也在行内展示，用布尔判颜色
  const [renewMsg, setRenewMsg] = useState<string | null>(null);
  const [renewOk, setRenewOk] = useState<boolean | null>(null);
  // R8-I-16：暂停/恢复失败用行内错误文案，不再 unhandled rejection
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  // R8-U-8：删除确认用应用内弹窗（A·零售式明亮克制风格），不再用 window.confirm
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const { state, availableCount, total, updatedAt, partialUnknown } = summarizeTask(task);
  const remaining = daysLeft(task.expires_at);
  const expired = state === 'expired' || (remaining !== null && remaining <= 0);
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
    setRenewOk(null);
    try {
      // R9-I14：后端续费/升级路径自动恢复 tier_limit 暂停任务，notices 在响应里返回，前端一并展示
      const t = await api.renewTask(task.id);
      const notices = t.notices ?? [];
      if (notices.length > 0) {
        // R10-P2-3：续期后任务从"已过期" tab 消失、卡片卸载，行内 renewMsg 会丢失；
        // 走首页横幅（location.state.notice）展示一次，与 AddMonitor 的 PATCH 失败模式一致
        onRenewNotice(notices.join('；'));
      }
      await onChanged();
    } catch (e) {
      setRenewMsg(e instanceof Error ? e.message : '续期失败');
      setRenewOk(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="p-4 rise-in">
      <Link to={`/tasks/${task.id}`} className="block">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-medium truncate">
              {task.name}
              {/* P2：确认后提醒模式加标识，用户能区分两种任务 */}
              {task.mode === 'confirmed' && (
                <span className="ml-2 text-[11px] px-1.5 py-0.5 rounded-pill bg-accent/10 text-accent font-medium align-middle">
                  确认后提醒
                </span>
              )}
            </p>
            <p className="mt-1 text-xs text-sub">
              <span className="product-name">{task.product_name}</span>
              {task.capacity && <> · {task.capacity}</>}
              {task.color && <> · {task.color}</>}
            </p>
            <p className="mt-0.5 text-xs text-faint">
              {/* P1：不裸显 part_number，上一行已有完整 SKU 名 */}
              {task.stores.length} 家门店
              {remaining !== null && !expired && (
                <span className={expiringSoon ? ' text-bad font-medium' : ''}>
                  {' · '}剩余 <span className="mono">{remaining}</span> 天
                </span>
              )}
            </p>
          </div>
          <div className="flex flex-col items-end gap-1.5 shrink-0">
            {task.paused && expired && <StockStateBadge state="expired" size="sm" />}
            <StockStateBadge state={state} historical={task.last_poll_ok === false || isResultStale(task, updatedAt)} />
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
              {state === 'paused' || state === 'expired' ? '已停止检查' : state === 'unknown' ? '库存尚未确认' : <>
                {task.last_poll_ok === false || isResultStale(task, updatedAt) ? '上次有货' : '有货'} <span className="mono text-ink">{availableCount}</span>{' / '}
                <span className="mono">{total}</span> 家门店
              </>}
            </span>
            {updatedAt && (
              <span>
                记录于 <span className="mono">{fmtTime(updatedAt)}</span>
              </span>
            )}
          </div>
        )}
        <CheckHealth task={task} />

      </Link>
      {/* R11-P1-4：Link 嵌套 Link 是非法 HTML（内层点击可能冒泡触发外层导航）——
          trialExhausted 提示移到外层 Link 之外，仍在卡片内 */}
      {trialExhausted && (
        <p className="mt-1.5 text-[11px] text-bad font-medium">
          免费版推送已用完，去
          {/* R6-I11：匿名用户文案"去注册"，已登录用户文案"去升级"
              （被 admin 授予 trial 的已注册用户也会命中横幅） */}
          <Link to={anonymous ? '/login' : '/me'} className="underline">
            {anonymous ? '注册' : '升级'}
          </Link>
          继续监控
        </p>
      )}
      <div className="mt-3 pt-3 border-t border-line">
        {/* R8-I-12：3 天内到期也渲染续期按钮，与详情页规则（expired || days<=3）一致；
            暂停按钮保留在下方第二排，不挤占 */}
        {expiringSoon && (
          <button
            onClick={renew}
            disabled={busy}
            className="w-full mb-2 py-2 rounded-card-sm bg-accent text-white text-[13px] font-medium active:scale-[0.98] transition disabled:opacity-40"
          >
            {busy ? '续期中…' : `续期（${anonymous ? '+24 小时' : '+30 天'}）· 剩余 ${remaining} 天`}
          </button>
        )}
        <div className="flex gap-2">
          {expired ? (
            <button
              onClick={renew}
              disabled={busy}
              className="flex-1 py-2 rounded-card-sm bg-accent text-white text-[13px] font-medium active:scale-[0.98] transition disabled:opacity-40"
            >
              {busy ? '续期中…' : `续期（${anonymous ? '+24 小时' : '+30 天'}）`}
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
          <p className={`mt-1.5 text-[11px] text-center ${renewOk ? 'text-ok' : 'text-bad'}`}>
            {renewMsg}
          </p>
        )}
        {/* R8-I-16：暂停/恢复、删除失败的行内错误文案 */}
        {actionMsg && (
          <p className="mt-1.5 text-[11px] text-center text-bad">{actionMsg}</p>
        )}
        {/* P2：暂停前就明示占用名额，不让用户事后才发现 */}

      </div>
      {/* R8-U-8：应用内删除确认（底部弹出卡片），替代 window.confirm */}
      {confirmingDelete && (
        <ConfirmDialog title="删除监控任务" busy={busy} onCancel={() => setConfirmingDelete(false)}>
            <p className="mt-2 text-sm text-sub leading-relaxed">
              删除“{task.name}”？停止监控且无法恢复。
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
        </ConfirmDialog>
      )}
    </Card>
  );
}

const TABS: { id: TaskStatusFilter; label: string }[] = [
  { id: 'active', label: '任务' },
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
  const [groupBusy, setGroupBusy] = useState<string | null>(null);
  const [groupMessage, setGroupMessage] = useState<string | null>(null);
  const groups = tasks.reduce<Map<string, Task[]>>((map, task) => {
    const key = task.group ? `group:${task.group}` : `task:${task.id}`;
    map.set(key, [...(map.get(key) ?? []), task]);
    return map;
  }, new Map());
  const toggleGroup = async (group: string, paused: boolean) => {
    setGroupBusy(group); setGroupMessage(null);
    try {
      const result = await api.groupAction(group, paused);
      setGroupMessage(`已${paused ? '暂停' : '恢复'} ${result.length} 个任务`);
      await reload();
    } catch (e) { setGroupMessage(e instanceof Error ? e.message : '操作失败，请重试'); }
    finally { setGroupBusy(null); }
  };
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

  // 免费版配额耗尽 → 任务卡提示"免费版推送已用完，去注册/升级"（断裂-9）
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
      <PageHeader title="监控" subtitle="Apple 直营店库存 · 北京时间" />
      <div className="px-4 pb-4">
        <div className="flex gap-2 mb-4">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              aria-pressed={tab === t.id}
              className={`px-4 py-2 rounded-pill text-sm font-medium whitespace-nowrap transition active:scale-95 ${
                tab === t.id ? 'bg-island text-white' : 'bg-white text-sub shadow-card'
              }`}
            >
              {t.label}
            </button>
          ))}
          <button onClick={() => reload()} disabled={loading} className="ml-auto text-sm text-accent disabled:opacity-40">{loading ? '刷新中…' : '刷新'}</button>
        </div>
        {loadError && <ErrorState message={loadError} onRetry={() => reload()} />}
        {!loadError && loading && tasks.length === 0 && <LoadingState rows={3} />}
        {!loadError && !loading && tasks.length === 0 && (
          <EmptyState
            title={tab === 'expired' ? '没有已过期的任务' : '还没有监控任务'}
            hint={tab === 'expired' ? '任务过期后会出现在这里' : '选择机型和门店，收到到货提醒。'}
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
            {groupMessage && <p role="status" className="text-sm text-sub">{groupMessage}</p>}
            {[...groups.entries()].map(([key, members]) => <section key={key} className="space-y-3">
              {members[0].group && <Card className="p-3">
                <h2 className="text-sm font-semibold break-words">{members[0].group}</h2>
                <p className="mt-1 text-xs text-sub">当前显示 {members.length} 个任务</p>
                {me !== null && <div className="mt-2 flex flex-wrap gap-x-4 gap-y-2">
                <button disabled={groupBusy !== null} onClick={() => void toggleGroup(members[0].group!, true)} className="text-sm text-accent disabled:opacity-40">暂停整组</button>
                <button disabled={groupBusy !== null} onClick={() => void toggleGroup(members[0].group!, false)} className="text-sm text-accent disabled:opacity-40">恢复整组</button>
                <Link to={`/group?name=${encodeURIComponent(members[0].group)}`} className="text-sm text-accent">分组设置</Link>
                </div>}
                <details className="mt-2 text-xs text-sub"><summary className="cursor-pointer">操作范围</summary><p className="mt-1">作用于组内全部任务。暂停仍占名额，已到期任务需单独续期。</p></details>
              </Card>}
              {members.map((t) => (
              <TaskCard
                key={t.id}
                task={t}
                onChanged={() => reload()}
                trialExhausted={trialExhausted}
                anonymous={me === null}
                onRenewNotice={(msg) =>
                  navigate(location.pathname, { state: { notice: msg } })
                }
              />
            ))}
            </section>)}
          </div>
        )}
      </div>
    </div>
  );
}
