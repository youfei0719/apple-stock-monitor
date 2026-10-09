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
      <PageHeader title={guide?.title ?? '到货购买指南'} subtitle="抢到有货之后，这样最快下单" />
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
        <Card className="mt-4 p-4 bg-island text-white rise-in">
          <p className="text-[15px] font-medium">关键提醒</p>
          <p className="mt-2 text-sm text-white/80 leading-relaxed">
            店内取货名额按门店实时释放，看到「有货」请在 5 分钟内完成下单；下单时选择「店内取货」并确认取货门店与监控的门店一致。
          </p>
        </Card>
      </div>
    </div>
  );
}
