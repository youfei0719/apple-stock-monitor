import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, cleanChannels, isResultStale, isTaskExpired, summarizeTask, type StateRow, type StoreRef, type Task, type TaskChannels } from '../lib/api';
import { useApp } from '../components/App';
import CheckHealth from '../components/CheckHealth';
import StockStateBadge from '../components/StockStateBadge';
import NotifyChannels, { NotificationHistory } from '../components/NotifyChannels';
import { Card, ErrorState, LoadingState, EmptyState, PageHeader } from '../components/ui';

const MS_PER_DAY = 86400000;
/** 续期展示阈值：已过期，或 3 天内到期（N15） */
const RENEW_SOON_DAYS = 3;

export default function TaskDetail() {
  const { id } = useParams<{ id: string }>();
  const [rows, setRows] = useState<StateRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stores, setStores] = useState<StoreRef[] | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [channels, setChannels] = useState<TaskChannels>({});
  const [draftName, setDraftName] = useState('');
  const [draftGroup, setDraftGroup] = useState('');
  const [draftMode, setDraftMode] = useState<'instant' | 'confirmed'>('instant');
  const [repeatEnabled, setRepeatEnabled] = useState(false);
  const [repeatInterval, setRepeatInterval] = useState('300');
  const [draftStores, setDraftStores] = useState<StoreRef[]>([]);
  const [draftRevision, setDraftRevision] = useState<string | undefined>();
  const [conflict, setConflict] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  // R8-U-10：保存成功/失败用布尔状态判颜色，不再用 saveMsg.includes('已保存') 文本匹配
  const [saveOk, setSaveOk] = useState<boolean | null>(null);
  const [renewing, setRenewing] = useState(false);
  const [renewMsg, setRenewMsg] = useState<string | null>(null);
  // R9-I4：续期成功/失败用布尔状态判颜色，与 saveChannels 的 saveOk 对齐，
  // 不再用 renewMsg.includes('已续期') 文本匹配
  const [renewOk, setRenewOk] = useState<boolean | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const loadSequence = useRef(0);
  // F-N5：匿名 trial 任务后端续期钳制到 now+24h，文案按实际 expires_at 动态显示
  const { me } = useApp();
  const anonymous = me === null;
  const renewGainLabel = anonymous ? '+24 小时' : '+30 天';

  const load = async (preserveChannels = false) => {
    if (!id) return;
    const sequence = ++loadSequence.current;
    setRefreshing(true);
    setError(null);
    try {
      const [s, cat, t] = await Promise.all([
        api.taskStates(id),
        // 门店名/城市用 store_number 查 catalog 补
        api.stores().catch(() => [] as StoreRef[]),
        api.task(id),
      ]);
      if (sequence !== loadSequence.current) return;
      setRows(t ? t.stores.map((store) => s.find((row) => row.store_number === store.number && row.part_number === t.part_number) ?? {
        store_number: store.number, part_number: t.part_number,
        state: t.paused ? 'paused' : isTaskExpired(t) ? 'expired' : 'unknown',
      }) : s);
      setStores(cat);
      if (t) {
        setTask(t);
        if (!preserveChannels) {
          setDraftRevision(t.config_revision);
          setConflict(false);
          setChannels(t.channels ?? {});
          setDraftName(t.name);
          setDraftGroup(t.group ?? '');
          setDraftMode(t.mode);
          setRepeatEnabled(t.repeat_interval_sec !== null);
          setRepeatInterval(String(t.repeat_interval_sec ?? 300));
          setDraftStores(t.stores);
        }
      }
    } catch (e) {
      if (sequence !== loadSequence.current) return;
      setError(e instanceof Error ? e.message : '加载失败');
      setRows(null);
    } finally {
      if (sequence === loadSequence.current) setRefreshing(false);
    }
  };

  useEffect(() => {
    setTask(null);
    setRows(null);
    load();
    return () => { loadSequence.current++; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const saveChannels = async () => {
    if (!id) return;
    if (!draftName.trim() || !draftStores.length) {
      setSaveMsg('请填写任务名并至少选择一家门店'); setSaveOk(false); return;
    }
    const interval = Number(repeatInterval);
    if (repeatEnabled && (!Number.isInteger(interval) || interval < 60)) {
      setSaveMsg('重复提醒间隔至少 60 秒，且须为整数'); setSaveOk(false); return;
    }
    setSaving(true);
    setSaveMsg(null);
    setSaveOk(null);
    try {
      // R10-I4：提交前过滤空 webhook 行（与 AddMonitor 共用 lib.cleanChannels），
      // 空 url 后端 WebhookIn.url min_length=1 会 422
      const t = await api.updateTask(id, {
        config_revision: draftRevision,
        name: draftName.trim(), group: draftGroup.trim(), stores: draftStores, mode: draftMode,
        repeat_interval_sec: repeatEnabled ? interval : null, channels: cleanChannels(channels),
      });
      setTask(t);
      setDraftRevision(t.config_revision);
      setConflict(false);
      setSaveMsg('任务设置已保存');
      await load(true);
      setSaveOk(true);
    } catch (e) {
      setConflict(e instanceof Error && 'code' in e && e.code === 'config_conflict');
      setSaveMsg(e instanceof Error ? e.message : '保存失败');
      setSaveOk(false);
    } finally {
      setSaving(false);
    }
  };

  const renew = async () => {
    if (!id) return;
    setRenewing(true);
    setRenewMsg(null);
    setRenewOk(null);
    try {
      // 后端 renew 按 max(now, 原 expires_at) + 30 天算（匿名钳制 now+24h），
      // 文案按返回的实际 expires_at 动态给，不写死 +30 天
      const oldExp = task?.expires_at ? new Date(task.expires_at).getTime() : Date.now();
      const t = await api.renewTask(id);
      setTask(t);
      let msg: string;
      if (!t.expires_at) {
        msg = '已续期';
      } else {
        const gainedDays =
          (new Date(t.expires_at).getTime() - Math.max(Date.now(), oldExp)) / MS_PER_DAY;
        msg =
          gainedDays >= 29
            ? '已续期 +30 天'
            : gainedDays >= 0.9
              ? '已续期 +24 小时'
              : // R13-P1-1：非匿名任务真实续期必 ≥29 天（匿名钳制 +24h 必 ≥0.9），
                // 走到这里说明后端命中了续期去重（重复点击），不再显示"已续期至"假象
                !anonymous
                ? '请勿重复点击'
                : `已续期至 ${new Date(t.expires_at).toLocaleDateString('zh-CN', {
                    // R9-I7：全站显式北京时间（与页脚承诺一致）
                    timeZone: 'Asia/Shanghai',
                    month: '2-digit',
                    day: '2-digit',
                  })}`;
      }
      // R9-I14：后端续费/升级路径自动恢复 tier_limit 暂停的任务，notices 在响应里返回，
      // 前端一并展示（如"已自动恢复 N 个任务"）
      const notices = t.notices ?? [];
      if (notices.length > 0) msg += `；${notices.join('；')}`;
      setRenewMsg(msg);
      setRenewOk(true);
    } catch (e) {
      setRenewMsg(e instanceof Error ? e.message : '续期失败');
      setRenewOk(false);
    } finally {
      setRenewing(false);
    }
  };
  const storeMap = useMemo(() => {
    const m = new Map<string, StoreRef>();
    (stores ?? []).forEach((s) => m.set(s.number, s));
    return m;
  }, [stores]);

  const counts = (rows ?? []).reduce<Record<string, number>>((acc, r) => {
    acc[r.state] = (acc[r.state] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div>
      <PageHeader title={task?.name ?? '库存状态'} subtitle="门店库存 · 北京时间" />
      <div className="px-4 pb-4">
        <div className="flex items-center justify-between mb-3">
          <Link to="/" className="inline-block text-sm text-accent">
            ← 返回监控列表
          </Link>
          {/* P1：会员续费入口——任务页也能直达 /me，不用靠底部导航找 */}
          <Link to="/me" className="text-xs text-faint">
            会员与续费 →
          </Link>
        </div>
        {task && (() => {
          // N19：任务展示态统一走 summarizeTask（与后端 _display_state 一致的聚合口径）
          const sum = summarizeTask(task);
          const days = task.expires_at
            ? Math.ceil((new Date(task.expires_at).getTime() - Date.now()) / MS_PER_DAY)
            : null;
          const expired = sum.state === 'expired' || (days !== null && days <= 0);
          const showRenew = expired || (days !== null && days <= RENEW_SOON_DAYS);
          return (
            <div className="mb-4">
              <div className="flex items-center justify-between gap-3">
                <StockStateBadge state={sum.state} size="sm" historical={task.last_poll_ok === false || isResultStale(task, sum.updatedAt)} />
                {task.mode === 'confirmed' && (
                  <span className="text-[11px] px-1.5 py-0.5 rounded-pill bg-accent/10 text-accent font-medium">
                    连续确认
                  </span>
                )}
              </div>
              {/* P1：展示任务配置（机型/门店），用户能核对自己选了什么 */}
              <Card className="mt-3 p-3.5">
                <p className="text-[13px] font-medium">
                  {task.product_name}
                  {task.capacity && <> · {task.capacity}</>}
                  {task.color && <> · {task.color}</>}
                </p>
                <p className="mt-1 text-xs text-sub">任务创建于 {new Date(task.created_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}（北京时间）</p>
                <p className="mt-1 text-xs text-sub">
                  {/* P1：不裸显 part_number，上一行已有完整 SKU 名 */}
                  {task.stores.length} 家门店：
                  {task.stores.map((s) => s.name?.replace('Apple ', '') || s.number).join('、')}
                </p>
              </Card>
              <CheckHealth task={task} />
              {showRenew && (
                <div className="mt-3">
                  <button
                    onClick={renew}
                    disabled={renewing}
                    className="w-full py-2.5 rounded-card-sm bg-accent text-white text-sm font-medium active:scale-[0.99] transition disabled:opacity-40"
                  >
                    {renewing
                      ? '续期中…'
                      : expired
                        ? `续期（${renewGainLabel}）`
                        : `续期（${renewGainLabel}）· 剩余 ${days} 天`}
                  </button>
                  {renewMsg && (
                    <p
                      className={`mt-1.5 text-xs text-center ${
                        // R9-I4：布尔判颜色，不再文本匹配
                        renewOk ? 'text-ok' : 'text-bad'
                      }`}
                    >
                      {renewMsg}
                    </p>
                  )}
                </div>
              )}
            </div>
          );
        })()}
        <div className="mb-4">
          <button
            onClick={() => load(true)}
            disabled={refreshing}
            className="text-sm text-accent disabled:opacity-40"
          >
            {refreshing ? '刷新中…' : '刷新检查结果'}
          </button>
          <p className="mt-1 text-xs text-sub">库存自动检查，刷新可读取最新记录。</p>
        </div>
        {error && <ErrorState message={error} onRetry={() => load(true)} />}
        {!error && rows === null && <LoadingState rows={4} />}
        {!error && rows !== null && rows.length === 0 && (
          <EmptyState
            title={task?.paused ? '任务已暂停' : task && isTaskExpired(task) ? '任务已到期' : '等待首次检查'}
            hint={task?.paused ? '恢复后继续检查库存。' : task && isTaskExpired(task) ? '续期后继续检查库存。' : '库存检查完成后会显示在这里。'}
            action={null}
          />
        )}
        {!error && rows !== null && rows.length > 0 && (
          <>
            <p className="text-xs text-sub mb-2">门店检查结果</p>
            <div className="flex flex-wrap gap-2 mb-4">
              {(
                [
                  'available',
                  'unavailable',
                  'unknown',
                  'verifying',
                  'cooling',
                  'paused',
                  'expired',
                ] as const
              )
                .filter((s) => counts[s])
                .map((s) => (
                  <span key={s} className="flex items-center gap-1.5">
                    <StockStateBadge state={s} size="sm" historical={task !== null && (task.last_poll_ok === false || isResultStale(task, summarizeTask(task).updatedAt))} />
                    <span className="mono text-xs text-sub">{counts[s]}</span>
                  </span>
                ))}
            </div>
            <div className="space-y-2.5">
              {rows.map((r, i) => {
                const info = storeMap.get(r.store_number);
                return (
                  <Card key={i} className="p-3.5 rise-in">
                    <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-[15px] font-medium truncate">
                        {info?.name || r.store_number}
                        {info?.city && (
                          <span className="text-sub font-normal"> · {info.city}</span>
                        )}
                      </p>
                      <p className="mt-0.5 text-xs text-faint">
                        {/* P1：不裸显 part_number（任务单 SKU，此处冗余） */}
                        {/* P1：pickup_display 是苹果 API 原始值（available/unavailable），
                            不直接裸显英文，映射为中文 */}
                        {r.pickup_display === 'available' && ['available', 'verifying', 'paused', 'expired'].includes(r.state) && <>{r.state === 'paused' || r.state === 'expired' || (task && (task.last_poll_ok === false || isResultStale(task, r.updated_at))) ? '上次结果：可店内取货' : '可店内取货'}</>}
                        {r.pickup_display === 'unavailable' && ['paused', 'expired'].includes(r.state) && <>上次结果：不可店内取货</>}
                      </p>
                    </div>
                    <div className="flex flex-col items-end gap-1 shrink-0">
                      <StockStateBadge state={r.state} historical={task !== null && (task.last_poll_ok === false || isResultStale(task, r.updated_at))} />
                      {r.state === 'cooling' && (
                        <span className="text-[10px] text-warn">数据可能过期</span>
                      )}
                      <span className="mono text-xs text-faint">
                        查询于{' '}
                        {r.updated_at
                          ? // R9-I7：全站显式北京时间（与页脚承诺一致）
                            // P2：格式与 /me、/history 统一为"MM/DD HH:mm"
                            new Date(r.updated_at).toLocaleString('zh-CN', {
                              timeZone: 'Asia/Shanghai',
                              month: '2-digit',
                              day: '2-digit',
                              hour: '2-digit',
                              minute: '2-digit',
                            })
                          : ''}
                      </span>
                    </div>
                    </div>
                    {task && task.stores.length > 1 && <CheckHealth task={task} updatedAt={r.updated_at ?? null} />}
                  </Card>
                );
              })}
            </div>
          </>
        )}
        {/* 通知渠道配置（与添加监控页同一套表单） */}
        {!error && task !== null && (
          <details className="mt-4 mb-5">
            <summary className="text-base font-semibold cursor-pointer mb-3">编辑任务</summary>
            <Card className="p-4 mb-4 space-y-3">
              <label className="block text-sm">任务名
                <input value={draftName} maxLength={255} onChange={(e) => setDraftName(e.target.value)} className="mt-1 w-full p-3 rounded-card-sm bg-bg" />
              </label>
              <label className="block text-sm">分组（可选）
                <input maxLength={128} value={draftGroup} onChange={(e) => setDraftGroup(e.target.value)} className="mt-1 w-full p-3 rounded-card-sm bg-bg" />
              </label>
              <label className="block text-sm">到货提醒
                <select value={draftMode} onChange={(e) => setDraftMode(e.target.value as 'instant' | 'confirmed')} className="mt-1 w-full p-3 rounded-card-sm bg-bg">
                  <option value="instant">到货即提醒</option>
                  <option value="confirmed">确认后提醒</option>
                </select>
                <span className="block mt-1 text-xs text-sub">{draftMode === 'instant' ? '首次查到有货，或无货变有货时提醒。' : '连续两次查到有货后提醒。'}</span>
              </label>
              <label className="flex gap-2 text-sm items-center"><input type="checkbox" checked={repeatEnabled} onChange={(e) => setRepeatEnabled(e.target.checked)} />持续有货时重复提醒</label>
              {repeatEnabled && <label className="block text-sm">重复提醒间隔（秒）
                <input type="number" min={60} step={1} value={repeatInterval} onChange={(e) => setRepeatInterval(e.target.value)} className="mt-1 w-full p-3 rounded-card-sm bg-bg" />
              </label>}
              <p className="text-xs text-sub">每次发送占用 1 次提醒额度，用完后自动暂停。</p>
              {draftStores.length > 20 && <p role="alert" className="text-sm text-bad">最多选择 20 家门店，请减少选择后保存。</p>}
              <details>
                <summary className="text-sm cursor-pointer">监控门店（已选 {draftStores.length} 家）</summary>
                <p className="mt-2 text-xs text-sub">选择 1–20 家门店。新增门店将在下次检查时更新。</p>
                <div className="mt-2 max-h-64 overflow-y-auto space-y-2">
                  {[...new Map([...draftStores, ...(stores ?? [])].map((s) => [s.number, s])).values()].map((store) => <label key={store.number} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={draftStores.some((s) => s.number === store.number)} onChange={(e) => setDraftStores(e.target.checked ? [...draftStores, store] : draftStores.filter((s) => s.number !== store.number))} />
                    {store.name || store.number} · {store.city}
                  </label>)}
                </div>
              </details>
            </Card>
            <NotifyChannels value={channels} onChange={setChannels} />
            <button
              onClick={saveChannels}
              disabled={saving || conflict || draftStores.length > 20}
              className="mt-3 w-full py-2.5 rounded-card-sm bg-island text-white text-sm font-medium active:scale-[0.99] transition disabled:opacity-40"
            >
              {saving ? '保存中…' : '保存任务设置'}
            </button>
            {saveMsg && (
              <p className={`mt-1.5 text-xs text-center ${saveOk ? 'text-ok' : 'text-bad'}`}>
                {saveMsg}
              </p>
            )}
            {conflict && <button className="mt-2 text-sm text-accent" onClick={() => { setSaveMsg(null); setTask(null); setRows(null); void load(); }}>放弃修改，重新载入</button>}
          </details>
        )}
        {!error && task !== null && <div className="mt-5"><NotificationHistory taskId={id} /></div>}
      </div>
    </div>
  );
}
