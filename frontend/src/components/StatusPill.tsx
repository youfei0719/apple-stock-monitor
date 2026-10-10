/**
 * 状态徽章（替代灵动岛）
 * 设计：浅底内联小徽章，融入页面标题栏，不悬浮不抢视觉
 *  - 平时：浅灰底，绿点 +「监控中 · N 个任务」
 *  - 有货：浅红底，红点 +「有货」，点击跳转到任务详情
 *  - 无任务：灰点「暂无监控任务」
 *  - 全暂停：灰点「已暂停」
 */
export default function StatusPill({
  taskCount,
  hasStock,
  allPaused,
  onTap,
  onStockTap,
}: {
  taskCount: number;
  hasStock: boolean;
  /** 全暂停时显示"已暂停"而非"监控中"，文案诚实 */
  allPaused?: boolean;
  onTap?: () => void;
  /** 有货时点击跳转（跳到该任务详情页） */
  onStockTap?: () => void;
}) {
  const handleClick = () => {
    if (hasStock) {
      onStockTap?.();
      return;
    }
    onTap?.();
  };

  const idle = taskCount === 0 && !hasStock;

  return (
    <div className="flex justify-center pt-3 pb-1">
      <button
        onClick={handleClick}
        aria-live="polite"
        className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-pill text-[12px] transition active:scale-95 ${
          hasStock
            ? 'bg-red-50 text-red-600 border border-red-200'
            : 'bg-black/[0.04] text-sub border border-black/[0.06]'
        }`}
      >
        {hasStock ? (
          <>
            <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
            <span className="font-medium">有货，点击查看</span>
          </>
        ) : idle ? (
          <>
            <span className="w-1.5 h-1.5 rounded-full bg-faint" />
            <span>暂无监控任务</span>
          </>
        ) : (
          <>
            <span className={`w-1.5 h-1.5 rounded-full ${allPaused ? 'bg-faint' : 'bg-accent'}`} />
            <span>
              {allPaused ? '已暂停' : '已开启'} · <span className="mono font-medium">{taskCount}</span> 个任务
            </span>
          </>
        )}
      </button>
    </div>
  );
}
