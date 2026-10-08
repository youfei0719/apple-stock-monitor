import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, type Product, type StoreRef, type Task, type TaskChannels } from '../lib/api';
import { useApp } from '../components/App';
import { Card, EmptyState, ErrorState, LoadingState, PageHeader, PrimaryButton } from '../components/ui';

const CATEGORIES = [
  { id: 'iphone', label: 'iPhone' },
  { id: 'ipad', label: 'iPad' },
  { id: 'mac', label: 'Mac' },
  { id: 'watch', label: 'Watch' },
];

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="text-[13px] font-semibold text-sub mb-2">{children}</h2>;
}

export default function AddMonitor() {
  const navigate = useNavigate();
  const { refreshTasks } = useApp();

  const [category, setCategory] = useState('iphone');
  const [products, setProducts] = useState<Product[] | null>(null);
  const [prodErr, setProdErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [expert, setExpert] = useState(false);
  const [expertText, setExpertText] = useState('');

  const [stores, setStores] = useState<StoreRef[] | null>(null);
  const [storeErr, setStoreErr] = useState<string | null>(null);
  const [pickedStores, setPickedStores] = useState<Set<string>>(new Set());
  const [storeQuery, setStoreQuery] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  const [nameTemplate, setNameTemplate] = useState('');
  const [group, setGroup] = useState('');
  const [mode, setMode] = useState<'instant' | 'confirmed'>('instant');
  const [barkKey, setBarkKey] = useState('');
  const [email, setEmail] = useState('');

  const [submitting, setSubmitting] = useState(false);
  const [submitErr, setSubmitErr] = useState<string | null>(null);

  const loadProducts = async () => {
    setProdErr(null);
    try {
      setProducts(await api.products(category));
    } catch (e) {
      setProducts(null);
      setProdErr(e instanceof Error ? e.message : '加载失败');
    }
  };

  const loadStores = async (refresh = 0) => {
    setStoreErr(null);
    if (refresh) setRefreshing(true);
    try {
      setStores(await api.stores(refresh));
    } catch (e) {
      setStores(null);
      setStoreErr(e instanceof Error ? e.message : '加载失败');
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    setPicked(new Set());
    loadProducts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [category]);
  useEffect(() => {
    loadStores();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const partNumbers = useMemo(() => {
    if (expert) {
      return expertText
        .split(/[\s,，;；\n]+/)
        .map((s) => s.trim().toUpperCase())
        .filter(Boolean);
    }
    return [...picked];
  }, [expert, expertText, picked]);

  const filteredStores = useMemo(() => {
    const q = storeQuery.trim().toLowerCase();
    if (!q || !stores) return stores ?? [];
    return stores.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.city.toLowerCase().includes(q) ||
        s.number.toLowerCase().includes(q),
    );
  }, [stores, storeQuery]);

  const toggle = (set: Set<string>, key: string): Set<string> => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  };

  const submit = async () => {
    setSubmitErr(null);
    const store_numbers = [...pickedStores];
    if (partNumbers.length === 0) {
      setSubmitErr('请先选择机型（或在专家模式填写 part number）');
      return;
    }
    if (store_numbers.length === 0) {
      setSubmitErr('请至少选择一家 Apple 直营店');
      return;
    }
    setSubmitting(true);
    try {
      const created = await api.batchTasks({
        part_numbers: partNumbers,
        store_numbers,
        name_template: nameTemplate.trim() || '{product} · {store}',
      });
      // batch 接口只接受 part_numbers/store_numbers/name_template，
      // mode / group / 渠道按契约走 PATCH 逐个补齐
      const channels: TaskChannels = {};
      if (barkKey.trim()) channels.bark_key = barkKey.trim();
      if (email.trim()) channels.email = email.trim();
      const patch: Partial<Task> = { mode };
      if (group.trim()) patch.group = group.trim();
      if (Object.keys(channels).length > 0) patch.channels = channels;
      if (mode !== 'instant' || patch.group || patch.channels) {
        await Promise.all(created.map((t) => api.updateTask(t.id, patch)));
      }
      await refreshTasks();
      navigate('/');
    } catch (e) {
      setSubmitErr(e instanceof Error ? e.message : '创建失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      <PageHeader title="添加监控" subtitle="机型 × 门店多维选择 · 支持批量生成" />
      <div className="px-4 pb-6 space-y-5">
        {/* 品类 */}
        <section>
          <SectionTitle>品类</SectionTitle>
          <div className="grid grid-cols-4 gap-2">
            {CATEGORIES.map((c) => (
              <button
                key={c.id}
                onClick={() => setCategory(c.id)}
                className={`py-2.5 rounded-card-sm text-sm font-medium transition active:scale-95 ${
                  category === c.id ? 'bg-island text-white' : 'bg-white text-sub shadow-card'
                }`}
              >
                {c.label}
              </button>
            ))}
          </div>
        </section>

        {/* 机型选择 / 专家模式 */}
        <section>
          <div className="flex items-center justify-between mb-2">
            <SectionTitle>机型</SectionTitle>
            <button
              onClick={() => setExpert((v) => !v)}
              className="text-xs text-accent font-medium"
            >
              {expert ? '切换为列表选择' : 'part number 专家模式'}
            </button>
          </div>
          {expert ? (
            <Card className="p-4">
              <textarea
                value={expertText}
                onChange={(e) => setExpertText(e.target.value)}
                placeholder={'每行一个，如：\nMJYC4CH/A\nMJYD4CH/A'}
                rows={4}
                className="w-full mono text-sm bg-transparent outline-none resize-none placeholder:text-faint"
              />
              <p className="mt-2 text-xs text-sub">
                已识别 <span className="mono text-ink">{partNumbers.length}</span> 个 part number
              </p>
            </Card>
          ) : prodErr ? (
            <ErrorState message={prodErr} onRetry={loadProducts} />
          ) : products === null ? (
            <LoadingState rows={2} />
          ) : products.length === 0 ? (
            <EmptyState title="该品类暂无机型" hint="稍后再试" action={null} />
          ) : (
            <div className="space-y-2 max-h-72 overflow-y-auto">
              {products.map((p) => {
                const on = picked.has(p.part_number);
                return (
                  <button
                    key={p.part_number}
                    onClick={() => setPicked(toggle(picked, p.part_number))}
                    className={`w-full text-left p-3.5 rounded-card-sm transition active:scale-[0.99] ${
                      on ? 'bg-island text-white' : 'bg-white shadow-card'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[15px] font-medium truncate">
                        <span className="product-name">{p.name}</span>
                      </span>
                      <span className={`mono text-sm shrink-0 ${on ? 'text-white/90' : 'text-sub'}`}>
                        ¥{p.price_cny.toLocaleString('zh-CN')}
                      </span>
                    </div>
                    <div className={`mt-1 text-xs ${on ? 'text-white/70' : 'text-faint'}`}>
                      {p.capacity && <>{p.capacity} · </>}
                      {p.color && <>{p.color} · </>}
                      <span className="mono">{p.part_number}</span>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </section>

        {/* 门店 */}
        <section>
          <div className="flex items-center justify-between mb-2">
            <SectionTitle>门店（{pickedStores.size} 家已选）</SectionTitle>
            <button
              onClick={() => loadStores(1)}
              disabled={refreshing}
              className="text-xs text-accent font-medium disabled:opacity-40"
            >
              {refreshing ? '刷新中…' : '在线刷新门店目录'}
            </button>
          </div>
          <input
            value={storeQuery}
            onChange={(e) => setStoreQuery(e.target.value)}
            placeholder="搜索城市 / 门店名 / 编号"
            className="w-full mb-2 px-4 py-3 rounded-card-sm bg-white shadow-card text-sm outline-none placeholder:text-faint"
          />
          {storeErr ? (
            <ErrorState message={storeErr} onRetry={() => loadStores()} />
          ) : stores === null ? (
            <LoadingState rows={2} />
          ) : (
            <div className="space-y-2 max-h-72 overflow-y-auto">
              {filteredStores.map((s) => {
                const on = pickedStores.has(s.number);
                return (
                  <button
                    key={s.number}
                    onClick={() => setPickedStores(toggle(pickedStores, s.number))}
                    className={`w-full text-left px-4 py-3 rounded-card-sm flex items-center justify-between transition active:scale-[0.99] ${
                      on ? 'bg-island text-white' : 'bg-white shadow-card'
                    }`}
                  >
                    <span className="text-[15px] truncate">
                      {s.name}
                      <span className={`text-xs ml-2 ${on ? 'text-white/70' : 'text-faint'}`}>
                        {s.city} · <span className="mono">{s.number}</span>
                      </span>
                    </span>
                    <span
                      className={`w-5 h-5 rounded-full border-2 shrink-0 ml-2 ${
                        on ? 'bg-ok border-ok' : 'border-line'
                      }`}
                    />
                  </button>
                );
              })}
              {filteredStores.length === 0 && (
                <EmptyState title="没有匹配的门店" hint="换个关键词试试" action={null} />
              )}
            </div>
          )}
        </section>

        {/* 任务配置 */}
        <section>
          <SectionTitle>任务配置</SectionTitle>
          <Card className="p-4 space-y-3">
            <input
              value={nameTemplate}
              onChange={(e) => setNameTemplate(e.target.value)}
              placeholder="任务命名模板，如：{product} · {store}"
              className="w-full px-3 py-2.5 rounded-card-sm bg-bg text-sm outline-none placeholder:text-faint"
            />
            <div className="flex gap-2">
              <input
                value={group}
                onChange={(e) => setGroup(e.target.value)}
                placeholder="分组（可选）"
                className="flex-1 px-3 py-2.5 rounded-card-sm bg-bg text-sm outline-none placeholder:text-faint"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              {(
                [
                  { id: 'instant', label: '即时提醒' },
                  { id: 'confirmed', label: '连续确认' },
                ] as const
              ).map((m) => (
                <button
                  key={m.id}
                  onClick={() => setMode(m.id)}
                  className={`py-2.5 rounded-card-sm text-sm font-medium transition active:scale-95 ${
                    mode === m.id ? 'bg-island text-white' : 'bg-bg text-sub'
                  }`}
                >
                  {m.label}
                </button>
              ))}
            </div>
            <input
              value={barkKey}
              onChange={(e) => setBarkKey(e.target.value)}
              placeholder="Bark Key（推送渠道，可选）"
              className="w-full px-3 py-2.5 rounded-card-sm bg-bg text-sm outline-none placeholder:text-faint mono"
            />
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="邮箱（推送渠道，可选）"
              className="w-full px-3 py-2.5 rounded-card-sm bg-bg text-sm outline-none placeholder:text-faint"
            />
          </Card>
        </section>

        {submitErr && (
          <p className="text-sm text-bad text-center">{submitErr}</p>
        )}

        <PrimaryButton onClick={submit} disabled={submitting}>
          {submitting
            ? '生成中…'
            : `批量生成 ${partNumbers.length} 机型 × ${pickedStores.size} 门店`}
        </PrimaryButton>
        <p className="text-center text-xs text-faint">
          共生成 <span className="mono">{partNumbers.length * pickedStores.size}</span> 个监控组合
        </p>
      </div>
    </div>
  );
}
