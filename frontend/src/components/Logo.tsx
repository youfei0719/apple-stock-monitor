/** StockMon Logo — A 版本（蛋形环），透明底、无汉字 */
export function LogoMark({ className = 'w-7 h-7' }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 256 256"
      fill="none"
      className={className}
      aria-hidden="true"
    >
      <path
        d="M 44 128 C 44 84 84 52 128 68 C 172 84 212 96 212 128 C 212 160 172 172 128 188 C 84 204 44 172 44 128 Z"
        stroke="currentColor"
        strokeWidth="18"
        strokeLinecap="round"
      />
      <circle cx="128" cy="128" r="16" fill="currentColor" />
    </svg>
  );
}

/** 启动页：Logo 描线动画 — 环自己画出来，圆点弹簧弹出 */
export function LogoSplash() {
  return (
    <div className="min-h-screen bg-bg flex items-center justify-center">
      <svg
        width="96"
        height="96"
        viewBox="0 0 256 256"
        fill="none"
        className="logo-splash"
        aria-label="加载中"
        role="img"
      >
        <path
          d="M 44 128 C 44 84 84 52 128 68 C 172 84 212 96 212 128 C 212 160 172 172 128 188 C 84 204 44 172 44 128 Z"
          stroke="currentColor"
          strokeWidth="18"
          strokeLinecap="round"
          pathLength="100"
          className="logo-splash-ring"
        />
        <circle cx="128" cy="128" r="16" fill="currentColor" className="logo-splash-dot" />
      </svg>
    </div>
  );
}
