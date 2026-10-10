import { useEffect, useId, useRef, useState, type ReactNode } from 'react';

/** 大圆角白卡片（A·零售式明亮克制） */
export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`bg-white rounded-card shadow-card ${className}`}>{children}</div>
  );
}

/** 骨架屏 */
export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`skeleton rounded-card-sm ${className}`} />;
}

/** 加载态：骨架屏列表 */
export function LoadingState({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3 rise-in" aria-busy="true" aria-label="加载中">
      {Array.from({ length: rows }).map((_, i) => (
        <Card key={i} className="p-4 space-y-2">
          <Skeleton className="h-5 w-3/5" />
          <Skeleton className="h-4 w-4/5" />
          <Skeleton className="h-4 w-2/5" />
        </Card>
      ))}
    </div>
  );
}

/** 空状态：中性铃铛图标（∅ 有"禁止"语义，像在警告用户） */
export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <Card className="p-10 text-center rise-in">
      <div className="mx-auto mb-4 w-14 h-14 rounded-full bg-bg flex items-center justify-center">
        <svg
          width="26"
          height="26"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="text-faint"
          aria-hidden="true"
        >
          <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.7 21a2 2 0 0 1-3.4 0" />
        </svg>
      </div>
      <p className="text-ink font-medium title-balanced">{title}</p>
      {hint && <p className="mt-2 text-sm text-sub title-balanced">{hint}</p>}
      {action && <div className="mt-5">{action}</div>}
    </Card>
  );
}

/** 错误态（不许白屏） */
export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  const [remaining, setRemaining] = useState(0);
  useEffect(() => {
    const seconds = Number(message.match(/请在 (\d+) 秒后重试/)?.[1] ?? 0);
    const until = Date.now() + seconds * 1000;
    setRemaining(seconds);
    if (!seconds) return;
    const timer = window.setInterval(() => setRemaining(Math.max(0, Math.ceil((until - Date.now()) / 1000))), 1000);
    return () => window.clearInterval(timer);
  }, [message]);
  return (
    <Card className="p-8 text-center rise-in">
      <div className="mx-auto mb-4 w-14 h-14 rounded-full bg-bg flex items-center justify-center">
        <span className="text-2xl text-bad">!</span>
      </div>
      <p role="alert" className="text-ink font-medium">{remaining > 0 ? '请求暂时受限' : '加载未完成'}</p>
      <p className="mt-2 text-sm text-sub break-all">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          disabled={remaining > 0}
          className="disabled:opacity-40 mt-5 px-6 py-2.5 rounded-pill bg-accent text-white text-sm font-medium active:scale-95 transition"
        >
          {remaining > 0 ? `${remaining} 秒后可重试` : '再试一次'}
        </button>
      )}
    </Card>
  );
}

/** 页头（大标题居中 + 视觉校正） */
export function PageHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="pt-2 pb-4 px-5 text-center">
      <h1 className="text-[22px] font-semibold tracking-tight title-balanced">{title}</h1>
      {subtitle && <p className="mt-1.5 text-sm text-sub title-balanced">{subtitle}</p>}
    </div>
  );
}

/** 主按钮 */
export function PrimaryButton({
  children,
  onClick,
  disabled,
  className = '',
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <button
      type={onClick ? 'button' : 'submit'}
      onClick={onClick}
      disabled={disabled}
      className={`w-full py-3.5 rounded-card-sm bg-accent text-white font-medium text-[15px] active:scale-[0.98] transition disabled:opacity-40 ${className}`}
    >
      {children}
    </button>
  );
}


/** 取消优先获取焦点，Esc 取消，Tab 保持在弹窗内。 */
export function ConfirmDialog({ title, children, busy, onCancel }: {
  title: string; children: ReactNode; busy: boolean; onCancel: () => void;
}) {
  const titleId = useId();
  const dialog = useRef<HTMLDivElement>(null);
  const cancel = useRef(onCancel);
  cancel.current = onCancel;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.querySelector<HTMLButtonElement>('button')?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, []);
  return <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 px-4 pb-10" onClick={() => !busy && cancel.current()}>
    <div ref={dialog} role="dialog" aria-modal="true" aria-labelledby={titleId} className="w-full max-w-lg bg-white rounded-card shadow-card-lg p-5 rise-in" tabIndex={-1} onClick={(event) => event.stopPropagation()} onKeyDown={(event) => {
      if (event.key === 'Escape') { event.preventDefault(); if (!busy) cancel.current(); }
      if (event.key !== 'Tab') return;
      const controls = [...(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), [tabindex="0"]') ?? [])];
      const first = controls[0], last = controls[controls.length - 1];
      if (!first) { event.preventDefault(); dialog.current?.focus(); }
      else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }}>
      <h2 id={titleId} className="font-medium">{title}</h2>
      {children}
    </div>
  </div>;
}
