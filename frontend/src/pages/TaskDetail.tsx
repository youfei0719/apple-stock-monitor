import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, type StateRow, type StoreRef } from '../lib/api';
import StockStateBadge from '../components/StockStateBadge';
import { Card, ErrorState, LoadingState, EmptyState, PageHeader } from '../components/ui';

export default function TaskDetail() {
  const { id } = useParams<{ id: string }>();
  const [rows, setRows] = useState<StateRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stores, setStores] = useState<StoreRef[] | null>(null);

  const load = async () => {
    if (!id) return;
    setError(null);
    try {
      const [s, cat] = await Promise.all([
        api.taskStates(id),
        // 门店名/城市用 store_number 查 catalog 补
        api.stores().catch(() => [] as StoreRef[]),
      ]);
      setRows(s);
      setStores(cat);
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
      setRows(null);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const storeMap = useMemo(() => {
    const m = new Map<string, StoreRef>();
    (stores ?? []).forEach((s) => m.set(s.number, s));
    return m;
  }, [stores]);

  const counts = (rows ?? []).reduce<Record<string, number>>((acc, r) => {
    acc[r.state] = (acc[r.state] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div>
      <PageHeader title="库存状态" subtitle="门店 × 配置 · 实时六态" />
      <div className="px-4 pb-4">
        <Link to="/" className="inline-block mb-3 text-sm text-accent">
          ← 返回监控列表
        </Link>
        {error && <ErrorState message={error} onRetry={load} />}
        {!error && rows === null && <LoadingState rows={4} />}
        {!error && rows !== null && rows.length === 0 && (
          <EmptyState title="暂无状态数据" hint="引擎可能还未完成第一轮查询" action={null} />
        )}
        {!error && rows !== null && rows.length > 0 && (
          <>
            <div className="flex flex-wrap gap-2 mb-4">
              {(['available', 'unavailable', 'unknown', 'verifying', 'cooling', 'paused'] as const)
                .filter((s) => counts[s])
                .map((s) => (
                  <span key={s} className="flex items-center gap-1.5">
                    <StockStateBadge state={s} size="sm" />
                    <span className="mono text-xs text-sub">{counts[s]}</span>
                  </span>
                ))}
            </div>
            <div className="space-y-2.5">
              {rows.map((r, i) => {
                const info = storeMap.get(r.store_number);
                return (
                  <Card key={i} className="p-3.5 flex items-center justify-between gap-3 rise-in">
                    <div className="min-w-0">
                      <p className="text-[15px] font-medium truncate">
                        {info?.name || r.store_number}
                        {info?.city && (
                          <span className="text-sub font-normal"> · {info.city}</span>
                        )}
                      </p>
                      <p className="mt-0.5 text-xs text-faint">
                        <span className="mono">{r.part_number}</span>
                        {r.pickup_display && <> · {r.pickup_display}</>}
                      </p>
                    </div>
                    <div className="flex flex-col items-end gap-1 shrink-0">
                      <StockStateBadge state={r.state} />
                      <span className="mono text-[10px] text-faint">
                        {r.updated_at
                          ? new Date(r.updated_at).toLocaleTimeString('zh-CN', {
                              hour: '2-digit',
                              minute: '2-digit',
                            })
                          : ''}
                      </span>
                    </div>
                  </Card>
                );
              })}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
