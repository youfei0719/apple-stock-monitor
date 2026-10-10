import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api, ApiError, type Task } from '../lib/api';
import { useApp } from '../components/App';
import { Card, ErrorState, LoadingState, PageHeader, PrimaryButton } from '../components/ui';

type EditableField = 'group' | 'mode' | 'repeat_interval_sec' | 'email';
const FIELD_LABELS: Record<EditableField, string> = {
  group: '分组名称', mode: '到货提醒', repeat_interval_sec: '重复提醒', email: '接收邮箱',
};
const inputClass = 'w-full mt-2 px-3 py-2.5 rounded-card-sm bg-bg text-sm';

export default function GroupSettings() {
  const [search] = useSearchParams();
  const group = search.get('name') ?? '';
  const navigate = useNavigate();
  const { me, refreshTasks } = useApp();
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<EditableField[]>([]);
  const [name, setName] = useState(group);
  const [mode, setMode] = useState<'instant' | 'confirmed'>('confirmed');
  const [repeat, setRepeat] = useState(false);
  const [interval, setInterval] = useState('300');
  const [email, setEmail] = useState('');
  const sequence = useRef(0);

  const load = async () => {
    const current = ++sequence.current;
    setError(null);
    setTasks(null);
    try {
      const result = await api.groupSettings(group);
      if (current !== sequence.current) return;
      setTasks(result);
      setSelected([]);
      setName(group);
      setMode(result[0].mode);
      setRepeat(result[0].repeat_interval_sec !== null);
      setInterval(String(result[0].repeat_interval_sec ?? 300));
      setEmail(result[0].channels.email ?? '');
      setConflict(false);
      setMessage(null);
    } catch (e) {
      if (current === sequence.current) setError(e instanceof Error ? e.message : '载入失败');
    }
  };
  useEffect(() => {
    setTasks(null);
    if (me && group) void load();
    return () => { sequence.current++; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group, me?.id]);

  const save = async () => {
    if (!tasks || !selected.length || busy) return;
    const seconds = Number(interval);
    if (selected.includes('group') && !name.trim()) {
      setMessage('请填写分组名称'); return;
    }
    if (selected.includes('repeat_interval_sec') && repeat && (!Number.isInteger(seconds) || seconds < 60)) {
      setMessage('提醒间隔需为整数，至少 60 秒'); return;
    }
    setBusy(true); setMessage(null);
    try {
      const patch = {
        ...(selected.includes('group') ? { group: name.trim() } : {}),
        ...(selected.includes('mode') ? { mode } : {}),
        ...(selected.includes('repeat_interval_sec') ? { repeat_interval_sec: repeat ? seconds : null } : {}),
        ...(selected.includes('email') ? { email: email.trim() || null } : {}),
      };
      const result = await api.updateGroupSettings(group, tasks.map((task) => ({
        id: task.id, config_revision: task.config_revision ?? '',
      })), patch);
      // 写入已成功，后续列表刷新失败不能改报为保存失败。
      void refreshTasks().catch(() => {});
      navigate('/', { state: { notice: `已更新“${result[0].group}”的 ${result.length} 个任务` } });
    } catch (e) {
      setConflict(e instanceof ApiError && e.code === 'config_conflict');
      setMessage(e instanceof Error ? e.message : '保存失败，请重试');
    } finally { setBusy(false); }
  };
  const mixed = (field: EditableField) => new Set(tasks?.map((task) => field === 'email' ? task.channels.email ?? '' : task[field])).size > 1;
  const choose = (field: EditableField) => <label className="flex items-center gap-2 font-medium text-sm">
    <input type="checkbox" checked={selected.includes(field)} disabled={busy} onChange={(event) => {
      setSelected((fields) => event.target.checked ? [...fields, field] : fields.filter((item) => item !== field));
      setMessage(null);
    }} />
    修改{FIELD_LABELS[field]}{mixed(field) && <span className="text-xs text-sub font-normal">设置不同</span>}
  </label>;

  return <div>
    <PageHeader title="分组设置" subtitle={group || '请先选择分组'} />
    <div className="px-4 space-y-3">
      <Link to="/" className="text-sm text-accent">← 返回监控列表</Link>
      {!me ? <Card className="p-5"><p>登录后可编辑分组。</p><Link to="/login" state={{ returnTo: `/group?name=${encodeURIComponent(group)}` }} className="text-accent">去登录</Link></Card>
        : !group ? <ErrorState message="请从监控列表选择分组。" />
        : error ? <ErrorState message={error} onRetry={() => void load()} />
        : !tasks ? <LoadingState rows={3} /> : <>
          <Card className="p-4">
            <h2 className="font-medium">共 {tasks.length} 个任务</h2>
            <p className="mt-2 text-sm text-sub">勾选要修改的设置，应用到整组。</p>
            <details className="mt-3 text-sm"><summary className="cursor-pointer text-accent">查看组内任务</summary>
              <ul className="mt-2 space-y-2">{tasks.map((task) => <li key={task.id} className="border-t border-line pt-2">
                <Link className="text-accent break-words" to={`/tasks/${task.id}`}>{task.name}</Link>
                <p className="text-xs text-sub">{[task.product_name || task.part_number, task.capacity, task.color].filter(Boolean).join(' · ')} · {task.stores.map((store) => store.name || store.number).join('、')}</p>
              </li>)}</ul>
              <p className="mt-2 text-xs text-sub">包含已暂停、已到期任务。修改设置不会恢复或续期。机型和门店可进入各任务修改。</p>
            </details>
          </Card>
          <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); void save(); }}>
            <Card className="p-4">{choose('group')}{selected.includes('group') && <label className="block mt-2 text-xs text-sub">分组名称<input className={inputClass} maxLength={128} value={name} disabled={busy} onChange={(event) => setName(event.target.value)} /></label>}</Card>
            <Card className="p-4">{choose('mode')}{selected.includes('mode') && <>
              <label className="block mt-2 text-xs text-sub">提醒方式<select className={inputClass} value={mode} disabled={busy} onChange={(event) => setMode(event.target.value as typeof mode)}><option value="instant">到货即提醒</option><option value="confirmed">确认后提醒</option></select></label>
              <p className="mt-2 text-xs text-sub">{mode === 'instant' ? '首次查到有货，或无货变有货时提醒。' : '连续两次查到有货后提醒。'}</p>
            </>}</Card>
            <Card className="p-4">{choose('repeat_interval_sec')}{selected.includes('repeat_interval_sec') && <>
              <label className="mt-3 flex gap-2 items-center text-sm"><input type="checkbox" checked={repeat} disabled={busy} onChange={(event) => setRepeat(event.target.checked)} />持续有货时重复提醒</label>
              {repeat && <label className="block mt-2 text-xs text-sub">提醒间隔（秒，至少 60）<input className={inputClass} type="number" min={60} step={1} required value={interval} disabled={busy} onChange={(event) => setInterval(event.target.value)} /></label>}
              <p className="mt-2 text-xs text-sub">{repeat ? '每次发送占用 1 次额度，发送速度受检查间隔限制。' : '只提醒新的到货。'}</p>
            </>}</Card>
            <Card className="p-4">{choose('email')}{selected.includes('email') && <label className="block mt-2 text-xs text-sub">接收邮箱<input className={inputClass} type="email" placeholder={me.email} value={email} disabled={busy} onChange={(event) => setEmail(event.target.value)} autoComplete="email" /><span className="block mt-1 text-xs text-sub">留空使用注册邮箱。</span></label>}</Card>
            {message && <p role="alert" className="text-sm text-bad">{message}</p>}
            {conflict && <button type="button" disabled={busy} className="text-sm text-accent" onClick={() => void load()}>放弃修改，重新载入</button>}
            <PrimaryButton disabled={busy || !selected.length || conflict}>{busy ? '保存中…' : `保存到 ${tasks.length} 个任务`}</PrimaryButton>
          </form>
        </>}
    </div>
  </div>;
}
