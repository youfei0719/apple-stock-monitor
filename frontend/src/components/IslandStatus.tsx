import { useState } from 'react';

/**
 * 灵动岛胶囊状态条
 * 设计依据：glint-vault 灵感Header动效-Dynamic-Island 卡
 *  - 平时：黑色胶囊收起态，绿点呼吸 +「监控中 · N 个任务」（数字用 Paper Mono）
 *  - 有货：胶囊展开 + 呼吸动效 →「有货！去查看」，点击跳转到该任务详情页
 *  - 无任务：灰点「暂无监控任务」
 * 形态切换走 width/border-radius 的 morph 过渡（cubic-bezier 参考 iOS Dynamic Island）
 */
export default function IslandStatus({
  taskCount,
  hasStock,
  onTap,
  onStockTap,
}: {
  taskCount: number;
  hasStock: boolean;
  onTap?: () => void;
  /** 有货时点击跳转（跳到该任务详情页） */
  onStockTap?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  const handleClick = () => {
    if (hasStock) {
      onStockTap?.();
      return;
    }
    setExpanded((v) => !v);
    onTap?.();
  };

  const idle = taskCount === 0 && !hasStock;

  return (
    <div className="fixed top-0 left-0 right-0 z-50 flex justify-center pointer-events-none">
      <button
        onClick={handleClick}
        aria-live="polite"
        className={`island pointer-events-auto mt-3 h-10 flex items-center justify-center gap-2 bg-island text-white shadow-island overflow-hidden ${
          hasStock ? 'island-hot rounded-[26px]' : 'rounded-pill'
        }`}
        style={{
          width: hasStock ? 232 : expanded ? 260 : idle ? 168 : 196,
        }}
      >
        <span className="island-content flex items-center gap-2 px-4 whitespace-nowrap">
          {hasStock ? (
            <>
              <span className="w-2 h-2 rounded-full bg-ok island-dot" />
              <span className="text-[13px] font-semibold tracking-wide">
                有货！去查看 →
              </span>
            </>
          ) : idle ? (
            <>
              <span className="w-2 h-2 rounded-full bg-faint" />
              <span className="text-[13px] text-white/80">暂无监控任务</span>
            </>
          ) : (
            <>
              <span className="w-2 h-2 rounded-full bg-ok island-dot" />
              <span className="text-[13px] text-white/90">
                监控中 · <span className="mono text-white">{taskCount}</span> 个任务
              </span>
            </>
          )}
        </span>
      </button>
    </div>
  );
}
