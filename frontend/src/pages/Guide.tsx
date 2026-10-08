import { useEffect, useState } from 'react';
import { api, type GuideSection } from '../lib/api';
import { Card, EmptyState, ErrorState, LoadingState, PageHeader } from '../components/ui';

export default function Guide() {
  const [sections, setSections] = useState<GuideSection[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number>(0);

  const load = async () => {
    setError(null);
    try {
      setSections(await api.guide());
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
      setSections(null);
    }
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <div>
      <PageHeader title="到货购买指南" subtitle="抢到有货之后，这样最快下单" />
      <div className="px-4 pb-4">
        {error && <ErrorState message={error} onRetry={load} />}
        {!error && sections === null && <LoadingState rows={3} />}
        {!error && sections !== null && sections.length === 0 && (
          <EmptyState title="指南暂未发布" hint="稍后再来看看" action={null} />
        )}
        {!error && sections !== null && sections.length > 0 && (
          <div className="space-y-3">
            {sections.map((s, i) => (
              <Card key={i} className="overflow-hidden rise-in">
                <button
                  onClick={() => setOpen(open === i ? -1 : i)}
                  className="w-full text-left p-4 flex items-center justify-between gap-3"
                >
                  <span className="text-[15px] font-medium title-balanced">
                    <span className="mono text-accent mr-2">{String(i + 1).padStart(2, '0')}</span>
                    {s.title}
                  </span>
                  <span className={`text-faint transition-transform ${open === i ? 'rotate-180' : ''}`}>
                    ▾
                  </span>
                </button>
                {open === i && (
                  <div className="px-4 pb-4 text-sm text-sub leading-relaxed whitespace-pre-wrap">
                    {s.body}
                  </div>
                )}
              </Card>
            ))}
          </div>
        )}
        <Card className="mt-4 p-4 bg-island text-white rise-in" >
          <p className="text-[15px] font-medium">关键提醒</p>
          <p className="mt-2 text-sm text-white/80 leading-relaxed">
            自提名额按门店实时释放，看到「有货」请在 5 分钟内完成下单；下单时选择「店内取货」并确认取货门店与 DING 监控的门店一致。
          </p>
        </Card>
      </div>
    </div>
  );
}
