import { useEffect, useState } from 'react';
import { isTaskExpired, launchTimeLabel, stockUnknownMessage, summarizeTask, type Task } from '../lib/api';

export default function CheckHealth({ task, updatedAt }: { task: Task; updatedAt?: string | null }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, []);
  const summary = summarizeTask(task);
  const interval = task.refresh_interval_sec ?? 300;
  const at = updatedAt !== undefined ? updatedAt : summary.updatedAt;
  const age = at ? Math.max(0, Math.floor((now - Date.parse(at)) / 1000)) : null;
  const stale = age !== null && (!Number.isFinite(age) || age > interval * 2);
  let message = '';
  let warning = false;
  if (task.paused) {
    const reasons: Record<string, string> = {
      quota_exhausted: '提醒额度已用完，任务已暂停。',
      notify_failures: '邮件连续发送失败，请核对邮箱后恢复。',
      tier_limit: '任务超出会员上限，请升级或减少任务。',
      zombie: '长期无货，已暂停。需要时可恢复。',
    };
    message = reasons[task.paused_reason ?? ''] ?? '';
  } else if (isTaskExpired(task)) {
    message = '已到期，续期后继续检查';
  } else if (summary.state === 'scheduled') {
    message = `已预约，${task.launch?.preorder_at ? launchTimeLabel(task.launch.preorder_at) + '起' : '开售后'}自动检查所选门店；确认可购买后提醒。`;
  } else if (summary.state === 'cooling') {
    message = '查询受限，稍后自动重试'; warning = true;
  } else if (task.last_poll_ok === false) {
    message = '库存未查完整，自动重试中'; warning = true;
  } else if (stale) {
    message = '结果未及时更新，请到 Apple 核实'; warning = true;
  } else if (age === null) {
    message = '等待首次检查';
  } else if (summary.unknownReason) {
    message = summary.partialUnknown ? '部分门店库存未确认' : stockUnknownMessage(summary.unknownReason);
  }
  const ageLabel = age !== null && Number.isFinite(age)
    ? age < 60 ? '刚刚查询' : age < 3600 ? `${Math.floor(age / 60)} 分钟前查询` : `${Math.floor(age / 3600)} 小时前查询`
    : '';
  return <div className={`mt-2 text-xs leading-relaxed ${warning ? 'text-[#9a5000]' : 'text-sub'}`}>
    {message && <p role="status">{message}</p>}
    {!task.paused && !isTaskExpired(task) && summary.state !== 'scheduled' && <p className={message ? 'mt-0.5' : ''}>
      {[ageLabel, `约每 ${interval < 60 ? `${interval} 秒` : `${Math.round(interval / 60)} 分钟`}检查`].filter(Boolean).join(' · ')}
    </p>}
  </div>;
}
