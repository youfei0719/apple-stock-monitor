import { useEffect, useState } from 'react';
import { api, type PurchaseGuide } from '../lib/api';
import { Card, EmptyState, ErrorState, LoadingState, PageHeader } from '../components/ui';

export default function Guide() {
  const [guide, setGuide] = useState<PurchaseGuide | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setError(null);
    try {
      // 后端 GET /guide/purchase 返回对象 {title, steps[]}，不是数组
      setGuide(await api.guide());
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
      setGuide(null);
    }
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <div>
      <PageHeader title={guide?.title ?? '到货购买指南'} subtitle="收到到货提醒后" />
      <div className="px-4 pb-4">
        {error && <ErrorState message={error} onRetry={load} />}
        {!error && guide === null && <LoadingState rows={3} />}
        {!error && guide !== null && guide.steps.length === 0 && (
          <EmptyState title="指南暂未发布" hint="稍后再来看看" action={null} />
        )}
        {!error && guide !== null && guide.steps.filter((s) => s.trim()).length > 0 && (
          <div className="space-y-3">
            {guide.steps
              .filter((s) => s.trim())
              .map((step, i) => (
              <Card key={i} className="p-4 rise-in">
                <div className="flex gap-3">
                  <span className="mono text-accent text-sm shrink-0">
                    {String(i + 1).padStart(2, '0')}
                  </span>
                  <p className="text-sm text-sub leading-relaxed">{step}</p>
                </div>
              </Card>
            ))}
          </div>
        )}
        <div className="mt-4 p-4 rounded-card bg-neutral-900 text-white rise-in shadow-card">
          <p className="text-[15px] font-medium">确认取货门店</p>
          <p className="mt-2 text-sm text-neutral-300 leading-relaxed">
            在 Apple 选择“店内取货”，核对门店后下单。库存随时变化，以 Apple 结账页为准。
          </p>
        </div>
      </div>
    </div>
  );
}
