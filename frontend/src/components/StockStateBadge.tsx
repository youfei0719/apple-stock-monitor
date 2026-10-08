import type { StockState } from '../lib/api';

/**
 * 七态库存徽章
 * 七态：available / unavailable / unknown / verifying / cooling / paused / expired
 * 设计要求：未知 ≠ 无货，必须有明确视觉区分
 *  - unknown：琥珀色 + 虚线边框「数据缺失」语义
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
    label: '未知',
    cls: 'bg-[#fff7e8] text-[#b25e09] border-dashed border-[#f0c36d]',
    dot: 'bg-warn',
  },
  verifying: {
    label: '确认中',
    cls: 'bg-[#e8f1fd] text-accent border-[#bcd6fa]',
    dot: 'bg-accent island-dot',
  },
  cooling: {
    label: '冷却中',
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
  withDot = true,
  size = 'md',
}: {
  state: StockState;
  withDot?: boolean;
  size?: 'sm' | 'md';
}) {
  const m = META[state] ?? META.unknown;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-pill border font-medium whitespace-nowrap ${
        size === 'sm' ? 'px-2 py-0.5 text-[11px]' : 'px-2.5 py-1 text-xs'
      } ${m.cls}`}
    >
      {withDot && <span className={`w-1.5 h-1.5 rounded-full ${m.dot}`} />}
      <span className={state === 'available' || state === 'verifying' ? 'mono' : ''}>
        {m.label}
      </span>
    </span>
  );
}
