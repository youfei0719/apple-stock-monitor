import { useEffect, useState } from 'react';
import { api, type NotificationRecord, type TaskChannels, type WebhookChannel } from '../lib/api';
import { Card, EmptyState, LoadingState } from './ui';

/**
 * 通知渠道配置（五通道）——任务详情 / 添加监控共用
 * - Bark key、邮箱、企微/钉钉/飞书 webhook URL（platform 下拉）
 * - 每个通道独立「发送测试」按钮（POST /notify/test，测试不扣配额）
 * - 可选：通知历史列表（含失败原因展示）
 * 到货通知附带直达商品页链接。
 */

const PLATFORMS = [
  { id: 'wecom', label: '企业微信' },
  { id: 'dingtalk', label: '钉钉' },
  { id: 'feishu', label: '飞书' },
];

const CHANNEL_LABEL: Record<string, string> = {
  bark: 'Bark',
  email: '邮件',
  wecom: '企业微信',
  dingtalk: '钉钉',
  feishu: '飞书',
  page: '站内',
  system: '站内',
};

const inputCls =
  'w-full px-3 py-2.5 rounded-card-sm bg-bg text-sm outline-none placeholder:text-faint';

function useTest() {
  const [busy, setBusy] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, { ok: boolean; msg: string }>>({});

  const run = async (key: string, channel: string, target: string) => {
    if (!target.trim()) {
      setResults((r) => ({ ...r, [key]: { ok: false, msg: '请先填写内容再测试' } }));
      return;
    }
    setBusy(key);
    try {
      const res = await api.notifyTest(channel, target.trim());
      setResults((r) => ({
        ...r,
        [key]: { ok: res.ok !== false, msg: res.message || (res.ok !== false ? '测试消息已发送' : '发送失败') },
      }));
    } catch (e) {
      setResults((r) => ({ ...r, [key]: { ok: false, msg: e instanceof Error ? e.message : '测试失败' } }));
    } finally {
      setBusy(null);
    }
  };

  return { busy, results, run };
}

function TestButton({
  onClick,
  busy,
  disabled,
}: {
  onClick: () => void;
  busy: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={busy || disabled}
      className="shrink-0 px-4 py-2.5 rounded-card-sm bg-island text-white text-[13px] font-medium active:scale-95 transition disabled:opacity-40"
    >
      {busy ? '发送中…' : '发送测试'}
    </button>
  );
}

function TestResult({ r }: { r?: { ok: boolean; msg: string } }) {
  if (!r) return null;
  return (
    <p className={`mt-1.5 text-xs ${r.ok ? 'text-ok' : 'text-bad'}`}>
      {r.ok ? '✓ ' : '✕ '}
      {r.msg}
    </p>
  );
}

export function NotificationHistory({ taskId }: { taskId?: string | number }) {
  const [items, setItems] = useState<NotificationRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setItems(null);
    setError(null);
    api
      .notifications(taskId)
      .then(setItems)
      .catch((e) => {
        setItems([]);
        setError(e instanceof Error ? e.message : '加载失败');
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  return (
    <div className="mt-4">
      <h3 className="text-[13px] font-semibold text-sub mb-2">通知历史</h3>
      {error && <p className="text-xs text-bad mb-2">{error}</p>}
      {items === null && <LoadingState rows={2} />}
      {items !== null && items.length === 0 && (
        <EmptyState title="暂无通知记录" hint="到货触发的通知会显示在这里" action={null} />
      )}
      {items !== null && items.length > 0 && (
        <div className="space-y-2 max-h-64 overflow-y-auto">
          {items.map((n) =>
            // D6：任务因连续 10 次通知失败被自动暂停 → 醒目提示 + 引导检查渠道配置
            n.kind === 'task_auto_paused' ? (
              <Card
                key={n.id}
                className="p-3.5 border-l-4 border-bad bg-[#fff5f5] rise-in"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-[13px] font-semibold text-bad">
                      ⚠ {n.title}
                    </p>
                    <p className="mt-1 text-xs text-sub leading-relaxed">{n.body}</p>
                    <p className="mt-1.5 text-xs text-ink font-medium">
                      请先检查通知渠道配置（Bark key / 邮箱 / webhook），确认能收到通知后再手动恢复任务。
                    </p>
                    <p className="mt-1 mono text-[10px] text-faint">
                      {new Date(n.created_at).toLocaleString('zh-CN', {
                        month: '2-digit',
                        day: '2-digit',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </p>
                  </div>
                  <span className="shrink-0 text-[11px] px-2 py-0.5 rounded-pill bg-bad/10 text-bad font-medium">
                    已自动暂停
                  </span>
                </div>
              </Card>
            ) : (
              <Card key={n.id} className="p-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-[13px] font-medium truncate">{n.title}</p>
                    <p className="mt-0.5 text-xs text-sub truncate">{n.body}</p>
                    <p className="mt-0.5 mono text-[10px] text-faint">
                      {CHANNEL_LABEL[n.channel] ?? n.channel}
                      {' · '}
                      {new Date(n.created_at).toLocaleString('zh-CN', {
                        month: '2-digit',
                        day: '2-digit',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </p>
                  </div>
                  {n.status === 'failed' ? (
                    <span className="shrink-0 text-[11px] px-2 py-0.5 rounded-pill bg-bad/10 text-bad font-medium">
                      发送失败
                    </span>
                  ) : n.status === 'skipped' ? (
                    <span className="shrink-0 text-[11px] px-2 py-0.5 rounded-pill bg-bg text-faint font-medium">
                      已跳过
                    </span>
                  ) : (
                    <span className="shrink-0 text-[11px] px-2 py-0.5 rounded-pill bg-ok/10 text-ok font-medium">
                      已送达
                    </span>
                  )}
                </div>
                {n.status === 'failed' && n.failure_reason && (
                  <p className="mt-1.5 text-xs text-bad bg-bad/5 rounded-card-sm px-2.5 py-1.5">
                    失败原因：{n.failure_reason}
                  </p>
                )}
              </Card>
            ),
          )}
        </div>
      )}
    </div>
  );
}

export default function NotifyChannels({
  value,
  onChange,
  showHistoryFor,
}: {
  value: TaskChannels;
  onChange: (c: TaskChannels) => void;
  /** 传入 task id 则渲染该任务的通知历史 */
  showHistoryFor?: string | number;
}) {
  const { busy, results, run } = useTest();

  const set = (patch: Partial<TaskChannels>) => onChange({ ...value, ...patch });

  const setWebhook = (idx: number, patch: Partial<WebhookChannel>) => {
    const list = [...(value.webhooks ?? [])];
    list[idx] = { ...list[idx], ...patch };
    set({ webhooks: list });
  };

  const addWebhook = () => {
    set({ webhooks: [...(value.webhooks ?? []), { url: '', platform: 'wecom' }] });
  };

  const removeWebhook = (idx: number) => {
    set({ webhooks: (value.webhooks ?? []).filter((_, i) => i !== idx) });
  };

  return (
    <div>
      <h3 className="text-[13px] font-semibold text-sub mb-2">通知渠道</h3>
      <Card className="p-4 space-y-4">
        <p className="-mt-1 text-xs text-faint leading-relaxed">
          到货时按这里的渠道发送通知（按实际发送成功的通知条数扣减配额），通知附带直达商品页链接。
        </p>
        {/* N14-B：当前档位仅邮件，Bark/群机器人可配但到货不发，明确标注避免误解 */}
        <p className="-mt-2 text-xs text-bad/90 leading-relaxed bg-bad/5 rounded-card-sm px-2.5 py-2">
          当前档位仅支持邮件；Bark / 群机器人可配置但到货不会发送（测试通过 ≠ 到货会发）。
        </p>

        {/* Bark */}
        <div>
          <div className="flex gap-2">
            <input
              value={value.bark_key ?? ''}
              onChange={(e) => set({ bark_key: e.target.value })}
              placeholder="Bark Key（可配置；当前档位到货不发送）"
              className={`${inputCls} mono`}
            />
            <TestButton
              busy={busy === 'bark'}
              onClick={() => run('bark', 'bark', value.bark_key ?? '')}
            />
          </div>
          <TestResult r={results.bark} />
        </div>

        {/* 邮箱 */}
        <div>
          <div className="flex gap-2">
            <input
              value={value.email ?? ''}
              onChange={(e) => set({ email: e.target.value })}
              placeholder="邮箱（推送渠道，可选）"
              type="email"
              className={inputCls}
            />
            <TestButton
              busy={busy === 'email'}
              onClick={() => run('email', 'email', value.email ?? '')}
            />
          </div>
          <TestResult r={results.email} />
        </div>

        {/* 群机器人 webhook */}
        <div>
          {(value.webhooks ?? []).map((w, i) => (
            <div key={i} className="mb-2.5">
              <div className="flex gap-2">
                <select
                  value={w.platform}
                  onChange={(e) => setWebhook(i, { platform: e.target.value })}
                  className="shrink-0 px-2.5 py-2.5 rounded-card-sm bg-bg text-sm outline-none"
                >
                  {PLATFORMS.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                </select>
                <input
                  value={w.url}
                  onChange={(e) => setWebhook(i, { url: e.target.value })}
                  placeholder="群机器人 webhook URL（可配置；当前档位到货不发送）"
                  className={`${inputCls} mono`}
                />
                <TestButton
                  busy={busy === `wh${i}`}
                  onClick={() => run(`wh${i}`, w.platform, w.url)}
                />
                <button
                  onClick={() => removeWebhook(i)}
                  className="shrink-0 px-3 py-2.5 rounded-card-sm bg-bg text-bad text-[13px] active:scale-95 transition"
                  aria-label="删除该 webhook"
                >
                  ✕
                </button>
              </div>
              <TestResult r={results[`wh${i}`]} />
            </div>
          ))}
          <button
            onClick={addWebhook}
            className="w-full py-2.5 rounded-card-sm border border-dashed border-line text-[13px] text-sub active:scale-[0.99] transition"
          >
            ＋ 添加群机器人（企业微信 / 钉钉 / 飞书）
          </button>
        </div>
      </Card>

      {showHistoryFor !== undefined && <NotificationHistory taskId={showHistoryFor} />}
    </div>
  );
}
