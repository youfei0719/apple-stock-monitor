import type { StockState } from '../lib/api';

/**
 * 库存状态徽章
 * 七种状态：available / unavailable / unknown / verifying / cooling / paused / expired
 * 设计要求：未知 ≠ 无货，必须有明确视觉区分
 *  - unknown：灰色「还没开始检测」（不是预警，不用琥珀色）
 *  - unavailable：灰色实心「确认无货」
 */

const META: Record<StockState, { label: string; cls: string; dot: string }> = {
  available: {
    label: '有货',
    cls: 'bg-[#e8f7ec] text-[#1a7f37] border-[#b9e6c5]',
    dot: 'bg-ok',
  },
  unavailable: {
    label: '无货',
    cls: 'bg-[#f2f2f4] text-sub border-transparent',
    dot: 'bg-faint',
  },
  unknown: {
    // P1：未知 ≠ 无货——虚线边框表示"还没开始检测"，与无货的实心灰区分
    label: '未知',
    cls: 'bg-white text-faint border border-dashed border-line',
    dot: 'bg-faint',
  },
  verifying: {
    label: '确认中',
    cls: 'bg-[#e8f1fd] text-accent border-[#bcd6fa]',
    dot: 'bg-accent island-dot',
  },
  cooling: {
    label: '等待恢复',
    cls: 'bg-[#fff1ea] text-[#c2410c] border-[#f5c6a5]',
    dot: 'bg-warn',
  },
  paused: {
    label: '已暂停',
    cls: 'bg-white text-faint border border-line',
    dot: 'bg-faint',
  },
  expired: {
    label: '已过期',
    cls: 'bg-[#ececee] text-[#6e6e73] border-transparent',
    dot: 'bg-[#aeaeb2]',
  },
};

export default function StockStateBadge({
  state,
  size = 'md',
  historical = false,
}: {
  state: StockState;
  // R10-死代码1：withDot prop 零调用方，已删除
  size?: 'sm' | 'md';
  historical?: boolean;
}) {
  const m = META[state] ?? META.unknown;
  // P1：cooling 状态给小白解释——查询太频繁被 Apple 暂时限制，稍后自动恢复，无需操作
  const title = state === 'cooling' ? '查询太频繁，Apple 暂时限制了访问，稍后自动恢复，无需你操作' : undefined;
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-pill border font-medium whitespace-nowrap ${
        size === 'sm' ? 'px-2 py-0.5 text-[11px]' : 'px-2.5 py-1 text-xs'
      } ${m.cls}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${m.dot}`} />
      <span className={state === 'available' || state === 'verifying' ? 'mono' : ''}>
        {historical && (state === 'available' || state === 'unavailable') ? `上次${m.label}` : m.label}
      </span>
    </span>
  );
}
