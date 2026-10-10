import { useEffect, useId, useState } from 'react';
import { api, ApiError, type NotificationRecord, type TaskChannels } from '../lib/api';
import { useApp } from './App';
import { Card, EmptyState, LoadingState } from './ui';

/**
 * R18-P3-5：通知渠道配置——仅邮件可用，其余通道暂未开放。任务详情 / 添加监控共用
 * - Bark key、邮箱、企微/钉钉/飞书 webhook URL（platform 下拉）
 * - 每个通道独立「发送测试」按钮（POST /notify/test，测试不扣配额）；匿名未登录时置灰
 * - 文案按档位动态：trial → 免费版仅站内通知（在本站内查看）
 * - 可选：通知历史列表（含失败原因展示）
 * 到货通知附带直达商品页链接。
 */

export const CHANNEL_LABEL: Record<string, string> = {
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
      const ok = res.ok !== false;
      // R6-U4：测试失败时优先展示后端返回的 error 原因（而不是吞掉只看 message）
      // P2：防御式转字符串——后端字段形态漂移时也不渲染 "[object Object]"
      const raw = res.error ?? res.message;
      const msg =
        typeof raw === 'string' && raw.trim()
          ? raw
          : ok
            ? '测试消息已发送'
            : '发送失败';
      setResults((r) => ({
        ...r,
        [key]: { ok, msg },
      }));
    } catch (e) {
      // R10-P2-6：档位不支持被拒（code=channel_not_supported）时统一文案
      // "该通道当前档位不支持，测试未执行"——不再直出后端那句自相矛盾的
      // "该测试只验证目标是否连通，不代表到货时会经此通道发送"
      const msg =
        e instanceof ApiError && e.code === 'channel_not_supported'
          ? '该通道当前档位不支持，测试未执行'
          : e instanceof Error
            ? e.message
            : '测试失败';
      setResults((r) => ({ ...r, [key]: { ok: false, msg } }));
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
                      请先检查邮箱配置，确认能收到通知后再手动恢复任务。
                    </p>
                    <p className="mt-1 mono text-[10px] text-faint">
                      {/* R8-U-1：后端返回 UTC 带 Z，显式按北京时间渲染（与 Me/History 一致） */}
                      {new Date(n.created_at).toLocaleString('zh-CN', {
                        timeZone: 'Asia/Shanghai',
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
                      {/* R8-U-1：后端返回 UTC 带 Z，显式按北京时间渲染（与 Me/History 一致） */}
                      {new Date(n.created_at).toLocaleString('zh-CN', {
                        timeZone: 'Asia/Shanghai',
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
                      {n.channel === 'page' || n.channel === 'system' ? '已记录' : '已发送'}
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
  const { me } = useApp();
  // UI-2：匿名未登录时"发送测试"按钮置灰（登录后可测试），别等 401 才生硬报错
  const anonymous = me === null;

  // F-2：通知渠道文案按档位动态。trial 只开放站内（page）；free/standard/pro 只开放邮件。
  // /quota 要求登录，匿名直接按 trial 渲染。
  // R10-I3（用户已拍板按档位校验直接 400）：后端对所有档位配 bark/webhook 直接 400，
  // tiers.py 无任何档位含 bark——"可配置""测试通过 ≠ 到货会发"是假话。Bark / 群机器人
  // 输入框直接禁用并注明"暂未开放"。
  const tier = me === null ? 'trial' : me?.tier ?? null;
  const channelNote =
    tier === 'trial'
      ? // UX：档位名全站统一叫「免费版」；本站是网页，没有 App
        // P2：后端真相 trial=["page"]、free/standard/pro=["email"]，无任何档位含
        // bark/webhook——"暂未开放"与"所有档位均不支持"自相矛盾，统一为"当前未开放"
        '体验任务仅在本站显示到货提醒。'
      : '到货后发送邮件，附商品链接。';

  const set = (patch: Partial<TaskChannels>) => onChange({ ...value, ...patch });

  const emailId = useId();
  const effectiveEmail = (value.email ?? '').trim() || (me?.email_verified ? me.email : '');
  const customEmail = !!me && effectiveEmail.toLowerCase() !== me.email.toLowerCase();

  return (
    <div>
      <h3 className="text-[13px] font-semibold text-sub mb-2">接收通知</h3>
      <Card className="p-4 space-y-4">

        {/* F-2：渠道开放范围按档位动态（trial → 免费版仅站内；付费档 → 仅邮件），不写死。
            R9-I11：tier===null（档位加载中）时不渲染档位文案，避免匿名用户首帧闪烁 */}
        {tier !== null && (
          // P2：这是说明信息不是报错，用中性底色，不用红色 alarming
          <p className="-mt-2 text-xs text-sub leading-relaxed bg-bg rounded-card-sm px-2.5 py-2">
            {channelNote}
          </p>
        )}
        {anonymous && (
          <p className="-mt-1 text-xs text-faint leading-relaxed">
            登录后可接收邮件。
          </p>
        )}

        {/* 邮箱——R11-P1-3：trial 档后端 _require_channels 对 email 直接 400，
            与 bark/webhook 禁用对称：trial 时禁用输入框并注明仅支持站内 */}
        <div>
          <label htmlFor={emailId} className="block text-sm font-medium mb-2">接收邮箱</label>
          <div className="flex gap-2">
            <input
              id={emailId}
              value={value.email ?? ''}
              onChange={(e) => set({ email: e.target.value })}
              placeholder={
                tier === 'trial' ? '登录后可使用邮件' : me?.email ?? '接收邮箱'
              }
              disabled={tier === 'trial'}
              title={
                tier === 'trial'
                  ? '体验任务仅支持站内通知（在本站内查看）'
                  : undefined
              }
              type="email"
              className={`${inputCls}${tier === 'trial' ? ' opacity-60' : ''}`}
            />
            <TestButton
              busy={busy === 'email'}
              disabled={anonymous || tier === 'trial' || customEmail}
              // P1：测试用"有效邮箱"——输入框为空但有已验证注册邮箱时，测注册邮箱；
              // 与下方"未填写时将使用你的注册邮箱"文案一致，不再自相矛盾
              onClick={() =>
                run('email', 'email', me?.email_verified ? me.email : '')
              }
            />
          </div>
          {/* P0：已验证邮箱自动算作邮件渠道——未填写时明确告诉用户发到哪里，
              避免"我没填邮箱，通知去哪了"的困惑 */}
          {!(value.email ?? '').trim() && me?.email_verified && tier !== 'trial' && (
            <p className="mt-1.5 text-[11px] text-faint">
              默认发往 {me.email}
            </p>
          )}
          {/* P1：测试邮箱限制事前说明，不让用户靠试错发现 */}
          {tier !== 'trial' && customEmail && (
            <p className="mt-1.5 text-[11px] text-faint">
              其他邮箱可保存；测试邮件仅能发到注册邮箱。
            </p>
          )}
          {!anonymous && !customEmail && <p className="mt-1 text-xs text-sub">测试邮件不占提醒额度。</p>}
          <TestResult r={results.email} />
        </div>

        {(value.bark_key || (value.webhooks ?? []).length > 0) && <div className="text-xs text-sub">
          <p>请先移除已停用的通知渠道。</p>
          <button onClick={() => onChange({ email: value.email })} className="mt-2 text-accent">移除旧渠道</button>
        </div>}
      </Card>

      {showHistoryFor !== undefined && <NotificationHistory taskId={showHistoryFor} />}
    </div>
  );
}
