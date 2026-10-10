import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError, cleanChannels, type Product, type Quota, type StoreRef, type TaskChannels } from '../lib/api';
import { useApp } from '../components/App';
import NotifyChannels from '../components/NotifyChannels';
import { Card, EmptyState, ErrorState, LoadingState, PageHeader, PrimaryButton } from '../components/ui';

/** 与 TaskCreateIn / TaskBatchIn 的 ge=60 对齐。 */
const MIN_REPEAT_INTERVAL_SEC = 60;

const CATEGORIES: { id: string; label: string; soon?: boolean; soonReason?: string }[] = [
  { id: 'iphone', label: 'iPhone' },
  { id: 'ipad', label: 'iPad', soon: true, soonReason: 'iPad 产品库正在建设中，暂只支持 iPhone' },
  { id: 'mac', label: 'Mac', soon: true, soonReason: 'Mac 产品库正在建设中，暂只支持 iPhone' },
  { id: 'watch', label: 'Watch', soon: true, soonReason: 'Watch 产品库正在建设中，暂只支持 iPhone' },
];

/** 向导步骤 */
const STEPS = ['选机型', '选配置', '选门店', '确认创建'] as const;

function Stepper({ step }: { step: number }) {
  return (
    <div className="flex gap-1.5 mb-5">
      {STEPS.map((label, i) => (
        <div key={label} className="flex-1">
          <div
            className={`h-1 rounded-full transition ${
              i < step ? 'bg-island' : i === step ? 'bg-accent' : 'bg-line'
            }`}
          />
          <p
            className={`mt-1.5 text-[11px] text-center ${
              i === step ? 'text-ink font-semibold' : 'text-faint'
            }`}
          >
            {label}
          </p>
        </div>
      ))}
    </div>
  );
}

function SectionQ({ children }: { children: React.ReactNode }) {
  return <h2 className="text-[17px] font-semibold mb-1">{children}</h2>;
}

function SectionHint({ children }: { children: React.ReactNode }) {
  return <p className="text-[13px] text-sub mb-3">{children}</p>;
}

export default function AddMonitor() {
  const navigate = useNavigate();
  const { refreshTasks, me, tasks } = useApp();
  const [quota, setQuota] = useState<Quota | null>(null);
  const [quotaError, setQuotaError] = useState<string | null>(null);
  const loadQuota = async () => {
    setQuotaError(null);
    try { setQuota(await api.quota()); }
    catch (error) { setQuotaError(error instanceof Error ? error.message : '额度载入失败'); }
  };
  const [allTaskCount, setAllTaskCount] = useState<number | null>(null);
  useEffect(() => {
    if (me) void loadQuota();
    api
      .tasks('all')
      .then((t) => setAllTaskCount(t.length))
      .catch(() => setAllTaskCount(null));
  }, [me]);
  const isAnon = me === null;
  const canRefreshCatalog = me?.is_admin === true;

  // 向导状态
  const [step, setStep] = useState(0);
  const stepPanel = useRef<HTMLDivElement>(null);
  const previousStep = useRef(0);
  useEffect(() => {
    if (previousStep.current === step) return;
    previousStep.current = step;
    window.scrollTo({ top: 0 });
    stepPanel.current?.focus({ preventScroll: true });
  }, [step]);
  const [category, setCategory] = useState('iphone');
  const [products, setProducts] = useState<Product[] | null>(null);
  const [prodErr, setProdErr] = useState<string | null>(null);

  // Step 1: 选机型（按产品名分组）
  const [selectedModel, setSelectedModel] = useState<string | null>(null);

  // Step 2: 选配置（容量/颜色多选）
  const [selectedCaps, setSelectedCaps] = useState<Set<string>>(new Set());
  const [selectedColors, setSelectedColors] = useState<Set<string>>(new Set());
  const [expert, setExpert] = useState(false);
  const [expertText, setExpertText] = useState('');

  // Step 3: 选门店
  const [stores, setStores] = useState<StoreRef[] | null>(null);
  const [storeErr, setStoreErr] = useState<string | null>(null);
  const [pickedStores, setPickedStores] = useState<Set<string>>(new Set());
  const [storeQuery, setStoreQuery] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [storeToast, setStoreToast] = useState<string | null>(null);

  // Step 4: 配置
  const [nameTemplate, setNameTemplate] = useState('');
  const [groupName, setGroupName] = useState('');
  const [mode, setMode] = useState<'instant' | 'confirmed'>('instant');
  const [repeatInterval, setRepeatInterval] = useState('300');
  const [repeatEnabled, setRepeatEnabled] = useState(false);
  const [channels, setChannels] = useState<TaskChannels>({});

  const [submitting, setSubmitting] = useState(false);
  const [submitErr, setSubmitErr] = useState<string | null>(null);
  const [showUpgradeLink, setShowUpgradeLink] = useState(false);

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
    setStoreToast(null);
    if (refresh) setRefreshing(true);
    try {
      setStores(await api.stores(refresh));
    } catch (e) {
      if (e instanceof ApiError && (e.status === 403 || e.status === 429 || e.code === 'refresh_limited')) {
        if (refresh) {
          setStoreToast(
            e.status === 429 || e.code === 'refresh_limited'
              ? '刷新太频繁，请稍后再试'
              : '门店目录刷新仅管理员可用',
          );
        } else {
          setStoreErr(e.message);
        }
      } else {
        setStores(null);
        setStoreErr(e instanceof Error ? e.message : '加载失败');
      }
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    setSelectedModel(null);
    setSelectedCaps(new Set());
    setSelectedColors(new Set());
    setExpert(false);
    setExpertText('');
    loadProducts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [category]);
  useEffect(() => {
    loadStores();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 按产品名分组（Step 1 用）
  const modelGroups = useMemo(() => {
    const map = new Map<string, Product[]>();
    for (const p of products ?? []) {
      if (!map.has(p.name)) map.set(p.name, []);
      map.get(p.name)!.push(p);
    }
    return [...map.entries()].map(([name, items]) => ({
      name,
      count: items.filter((p) => p.part_number).length,
      unavailable: items.filter((p) => !p.part_number).length,
      minPrice: Math.min(...items.map((i) => i.price_cny ?? 0)),
    }));
  }, [products]);

  // 选中机型的所有变体（Step 2 用）
  const modelProducts = useMemo(() => {
    if (!selectedModel) return [];
    return (products ?? []).filter((p) => p.name === selectedModel);
  }, [products, selectedModel]);

  const capacities = useMemo(() => {
    const set = new Set<string>();
    modelProducts.forEach((p) => p.capacity && set.add(p.capacity));
    return [...set];
  }, [modelProducts]);

  const colors = useMemo(() => {
    const set = new Set<string>();
    modelProducts.forEach((p) => p.color && set.add(p.color));
    return [...set];
  }, [modelProducts]);

  // 容量和颜色需要显式选择，空选择不能悄悄变成全选。
  const selectedParts = useMemo(() => {
    if (expert) {
      return [...new Set(expertText
        .split(/[\s,，;；\n]+/)
        .map((s) => s.trim().toUpperCase())
        .filter(Boolean))];
    }
    if ((capacities.length > 0 && selectedCaps.size === 0) ||
        (colors.length > 0 && selectedColors.size === 0)) return [];
    return modelProducts
      .filter(
        (p) =>
          p.part_number && // 空 part_number 表示未验证，不可直接选
          (selectedCaps.size === 0 || (p.capacity && selectedCaps.has(p.capacity))) &&
          (selectedColors.size === 0 || (p.color && selectedColors.has(p.color))),
      )
      .map((p) => p.part_number);
  }, [expert, expertText, modelProducts, selectedCaps, selectedColors, capacities, colors]);

  // 用户选了容量/颜色，但对应 SKU 的 part_number 尚未验证（需手动输入）
  const needsManualPn = useMemo(() => {
    if (expert) return false;
    if (selectedCaps.size === 0 && selectedColors.size === 0) return false;
    const matched = modelProducts.filter(
      (p) =>
        (selectedCaps.size === 0 || (p.capacity && selectedCaps.has(p.capacity))) &&
        (selectedColors.size === 0 || (p.color && selectedColors.has(p.color))),
    );
    return matched.length > 0 && matched.every((p) => !p.part_number);
  }, [expert, modelProducts, selectedCaps, selectedColors]);

  const filteredStores = useMemo(() => {
    const q = storeQuery.trim().toLowerCase();
    if (!q || !stores) return stores ?? [];
    return stores.filter(
      (s) =>
        (s.name ?? '').toLowerCase().includes(q) ||
        (s.city ?? '').toLowerCase().includes(q) ||
        (s.number ?? '').toLowerCase().includes(q),
    );
  }, [stores, storeQuery]);

  const storesByCity = useMemo(() => {
    const map = new Map<string, StoreRef[]>();
    for (const s of filteredStores) {
      const city = s.city || '其他';
      if (!map.has(city)) map.set(city, []);
      map.get(city)!.push(s);
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b, 'zh-CN'));
  }, [filteredStores]);

  const toggle = (set: Set<string>, key: string): Set<string> => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  };

  const toggleCity = (cityStores: StoreRef[]) => {
    const nums = cityStores.map((s) => s.number);
    const allOn = nums.every((n) => pickedStores.has(n));
    const next = new Set(pickedStores);
    if (allOn) nums.forEach((n) => next.delete(n));
    else nums.forEach((n) => next.add(n));
    setPickedStores(next);
  };

  // 配额
  const combos = selectedParts.length * pickedStores.size;
  const tasksLimit = isAnon ? 1 : quota?.tasks_limit;
  const tasksUsed = isAnon
    ? (allTaskCount ?? tasks.length)
    : (quota?.tasks_used ?? allTaskCount ?? tasks.length);
  const atQuota = tasksLimit !== undefined && tasksUsed >= tasksLimit;
  const overQuota = combos > 0 && tasksLimit !== undefined && tasksUsed + combos > tasksLimit;
  const remainingTasks = tasksLimit === undefined ? null : Math.max(0, tasksLimit - tasksUsed);
  const minimumCombos = selectedParts.length * Math.max(1, pickedStores.size);
  const selectionOverQuota = remainingTasks !== null && minimumCombos > remainingTasks;

  // 步骤校验
  const canNext = () => {
    if (step === 0) return selectedModel !== null;
    if (step === 1) return selectedParts.length > 0 && !selectionOverQuota;
    if (step === 2) return pickedStores.size > 0 && !overQuota;
    return true;
  };

  const nextHint = () => {
    if (step === 0 && !selectedModel) return '先选一款机型';
    if (step === 1 && selectedParts.length === 0) return '请选择容量和颜色';
    if (step === 1 && selectionOverQuota) return '请减少配置或增加任务名额';
    if (step === 2 && overQuota) return '请减少门店或增加任务名额';
    if (step === 2 && pickedStores.size === 0) return '选至少一家门店';
    return null;
  };

  const submit = async () => {
    setSubmitErr(null);
    const store_numbers = [...pickedStores];
    if (selectedParts.length === 0) {
      setSubmitErr('请先选择机型配置');
      return;
    }
    if (store_numbers.length === 0) {
      setSubmitErr('请至少选择一家 Apple 直营店');
      return;
    }
    const ri = Number(repeatInterval);
    if (repeatEnabled) {
      if (!Number.isInteger(ri) || ri < MIN_REPEAT_INTERVAL_SEC) {
        setSubmitErr(`持续提醒间隔须为整数，至少 ${MIN_REPEAT_INTERVAL_SEC} 秒（1 分钟）`);
        return;
      }
    }
    if (overQuota) {
      setSubmitErr(
        `将超出任务上限（${tasksLimit} 个）：已有 ${tasksUsed} 个，本次需生成 ${combos} 个，请减少选择`,
      );
      setShowUpgradeLink(true);
      return;
    }
    setShowUpgradeLink(false);
    setSubmitting(true);
    try {
      const nameTpl = nameTemplate.trim() || '{part_name} × {store_name}';
      const repeatSec = repeatEnabled ? ri : null;
      const clean = cleanChannels(channels);
      let doneCount = 0;
      let existingCount = 0;
      if (isAnon) {
        const storeByNumber = new Map((stores ?? []).map((s) => [s.number, s]));
        const productByPn = new Map((products ?? []).map((p) => [p.part_number, p]));
        let createdCount = 0;
        try {
          for (const pn of selectedParts) {
            for (const sn of store_numbers) {
              const prod = productByPn.get(pn);
              const info = storeByNumber.get(sn);
              const partName = prod ? `${prod.name} ${prod.capacity ?? ''} ${prod.color ?? ''}`.trim() : pn;
              const storeName = info ? (info.name?.replace('Apple ', '') || sn) : sn;
              const taskName = nameTpl
                .replace('{part_number}', pn)
                .replace('{store_number}', sn)
                .replace('{part_name}', partName)
                .replace('{store_name}', storeName);
              const result = await api.createTask({
                name: taskName,
                group: groupName.trim(),
                category,
                part_number: pn,
                product_name: prod?.name ?? '',
                color: prod?.color ?? '',
                capacity: prod?.capacity ?? '',
                stores: [{ number: sn, name: info?.name ?? '', city: info?.city ?? '' }],
                mode,
                repeat_interval_sec: repeatSec,
                channels: clean,
              });
              createdCount++;
              if (result.creation_status === 'existing') existingCount++;
            }
          }
          doneCount = createdCount;
        } catch (e) {
          if (createdCount > 0) await refreshTasks('active');
          const msg = e instanceof Error ? e.message : '创建失败';
          setSubmitErr(
            createdCount > 0 ? `已创建 ${createdCount} 个任务，后续创建失败：${msg}` : msg,
          );
          return;
        }
      } else {
        const created = await api.batchTasks({
          part_numbers: selectedParts,
          store_numbers,
          name_template: nameTpl,
          group: groupName.trim(),
          category,
          mode,
          repeat_interval_sec: repeatSec,
          channels: clean,
        });
        doneCount = created.length;
        existingCount = created.filter((t) => t.creation_status === 'existing').length;
      }
      await refreshTasks('active').catch(() => undefined);
      navigate('/', {
        state: { notice: existingCount > 0
          ? `新建 ${doneCount - existingCount} 个任务，找到 ${existingCount} 个现有任务。现有任务保留原设置，请到详情核对或修改。`
          : `已创建 ${doneCount} 个监控任务` },
      });
    } catch (e) {
      setSubmitErr(e instanceof Error ? e.message : '创建失败');
    } finally {
      setSubmitting(false);
    }
  };

  const riNum = Number(repeatInterval);
  const showEstimate =
    repeatEnabled && Number.isInteger(riNum) && riNum >= MIN_REPEAT_INTERVAL_SEC;
  const monthlyEstimate = showEstimate
    ? Math.ceil((30 * 24 * 3600) / Math.max(riNum, quota?.refresh_interval_sec ?? 300)) *
      Math.max(1, pickedStores.size) *
      Math.max(1, selectedParts.length)
    : 0;

  return (
    <div>
      <PageHeader title="添加监控" subtitle={`第 ${step + 1} 步，共 ${STEPS.length} 步`} />
      <div ref={stepPanel} tabIndex={-1} aria-label={STEPS[step]} className="px-4 pb-28 outline-none">
        <Stepper step={step} />
        {quotaError && <div role="alert" className="mb-3 text-sm text-bad">额度载入失败，创建时会再次校验。<button onClick={() => void loadQuota()} className="ml-2 text-accent">重试</button></div>}

        {/* Step 0: 选机型 */}
        {step === 0 && (
          <section>
            <SectionQ>你要监控哪款 iPhone？</SectionQ>
            <SectionHint>可选择多个容量、颜色和门店。</SectionHint>
            <div className="grid grid-cols-4 gap-2 mb-4">
              {CATEGORIES.map((c) => (
                <button
                  key={c.id}
                  onClick={() => setCategory(c.id)}
                  disabled={c.soon}
                  title={c.soon ? c.soonReason : undefined}
                  className={`py-2 rounded-card-sm text-sm font-medium transition active:scale-95 flex flex-col items-center justify-center gap-0.5 ${
                    category === c.id ? 'bg-island text-white' : 'bg-white text-sub shadow-card'
                  } ${c.soon ? 'opacity-45' : ''}`}
                >
                  <span>{c.label}</span>
                  {c.soon && <span className="text-[10px] font-normal opacity-80">暂未支持</span>}
                </button>
              ))}
            </div>
            {/* P1：品类不可选的原因说清楚，不只写"暂未支持"四个字 */}

            {prodErr ? (
              <ErrorState message={prodErr} onRetry={loadProducts} />
            ) : products === null ? (
              <LoadingState rows={2} />
            ) : modelGroups.length === 0 ? (
              <EmptyState title="该品类暂无机型" hint="换个品类试试" action={null} />
            ) : (
              <div className="space-y-2.5">
                {modelGroups.map((g) => {
                  const on = selectedModel === g.name;
                  return (
                    <button
                      key={g.name}
                      onClick={() => setSelectedModel(g.name)}
                      aria-pressed={on}
                      className={`w-full text-left p-4 rounded-card transition active:scale-[0.99] border-2 ${
                        on ? 'bg-white border-accent shadow-card' : 'bg-white border-transparent shadow-card'
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-[17px] font-semibold product-name">{g.name}</span>
                        {on && <span className="text-accent text-lg">✓</span>}
                      </div>
                      <p className="mt-1 text-[13px] text-sub">
                        {g.count} 种可监控 · {g.unavailable} 种暂未开放 · <span className="mono">¥{g.minPrice.toLocaleString('zh-CN')} 起</span>
                      </p>
                    </button>
                  );
                })}
              </div>
            )}
          </section>
        )}

        {/* Step 1: 选配置 */}
        {step === 1 && (
          <section>
            <div className="flex items-center justify-between mb-1">
              <SectionQ>选具体配置</SectionQ>
              <button
                onClick={() => setExpert((v) => !v)}
                className="text-xs text-accent font-medium"
              >
                {expert ? '返回列表选择' : '高级：手动输入'}
              </button>
            </div>
            <SectionHint>{selectedModel} · 请分别选择容量和颜色，可多选</SectionHint>
            {expert ? (
              <Card className="p-4">
                <textarea
                  value={expertText}
                  onChange={(e) => setExpertText(e.target.value)}
                  placeholder={'每行一个，如：\nMJYC4CH/A\nMJYD4CH/A'}
                  rows={4}
                  className="w-full mono text-sm bg-transparent outline-none resize-none placeholder:text-faint"
                />
                {expertText.trim() ? (
                  <p className="mt-2 text-xs text-sub">
                    已识别 <span className="mono text-ink">{selectedParts.length}</span> 个配置
                  </p>
                ) : (
                  <p className="mt-2 text-xs text-faint">每行一个 Apple 商品编号</p>
                )}
              </Card>
            ) : (
              <>
                <div className="flex gap-4 text-xs text-accent">
                  <button onClick={() => {
                    setSelectedCaps(new Set(modelProducts.filter((p) => p.part_number).map((p) => p.capacity)));
                    setSelectedColors(new Set(modelProducts.filter((p) => p.part_number).map((p) => p.color)));
                  }}>全选可监控配置</button>
                  <button onClick={() => {
                    setSelectedCaps(new Set());
                    setSelectedColors(new Set());
                  }}>清空选择</button>
                </div>
                {capacities.length > 0 && (
                  <>
                    <p className="text-[13px] font-semibold text-sub mb-2 mt-4">容量</p>
                    <div className="flex flex-wrap gap-2">
                      {capacities.map((c) => {
                        const on = selectedCaps.has(c);
                        const price = modelProducts.find((p) => p.capacity === c)?.price_cny;
                        return (
                          <button
                            key={c}
                            aria-pressed={on}
                            onClick={() => setSelectedCaps(toggle(selectedCaps, c))}
                            className={`px-4 py-3 rounded-card-sm transition active:scale-95 border-2 ${
                              on ? 'bg-island text-white border-island' : 'bg-white border-transparent shadow-card'
                            }`}
                          >
                            <div className="text-[15px] font-semibold">{c}</div>
                            {price != null && (
                              <div className={`mono text-xs mt-0.5 ${on ? 'text-white/70' : 'text-sub'}`}>
                                ¥{price.toLocaleString('zh-CN')}
                              </div>
                            )}
                          </button>
                        );
                      })}
                    </div>
                  </>
                )}
                {colors.length > 0 && (
                  <>
                    <p className="text-[13px] font-semibold text-sub mb-2 mt-4">颜色</p>
                    <div className="flex flex-wrap gap-2">
                      {colors.map((c) => {
                        const on = selectedColors.has(c);
                        return (
                          <button
                            key={c}
                            aria-pressed={on}
                            onClick={() => setSelectedColors(toggle(selectedColors, c))}
                            className={`px-4 py-2.5 rounded-pill transition active:scale-95 border-2 ${
                              on ? 'bg-island text-white border-island' : 'bg-white border-transparent shadow-card text-sub'
                            }`}
                          >
                            <span className="text-sm font-medium">{c}</span>
                          </button>
                        );
                      })}
                    </div>
                  </>
                )}
                <details className="mt-4 text-xs text-sub">
                  <summary className="cursor-pointer">查看支持的配置</summary>
                  <ul className="mt-2 space-y-1 text-xs">
                    {modelProducts.map((p) => <li key={`${p.capacity}-${p.color}`} className="flex justify-between gap-2">
                      <span>{p.capacity} · {p.color}</span>
                      <span className={p.part_number ? 'text-ink' : 'text-sub'}>{p.part_number ? '可监控' : '暂未开放'}</span>
                    </li>)}
                  </ul>
                  {selectedCaps.size > 0 && selectedColors.size > 0 && <p className="mt-2 text-xs text-sub">
                    所选 {selectedParts.length} 种可监控，{modelProducts.filter((p) => !p.part_number && selectedCaps.has(p.capacity) && selectedColors.has(p.color)).length} 种暂不支持。
                  </p>}
                </details>
                {selectedParts.length > 0 && (
                  <Card className="mt-4 p-3.5 bg-bg">
                    <p className="text-xs text-sub">
                      已选 <span className="mono text-ink font-semibold">{selectedParts.length}</span> 种配置：
                    </p>
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      {modelProducts
                        .filter((p) => selectedParts.includes(p.part_number))
                        .map((p) => (
                          <span key={p.part_number} className="text-[11px] px-2 py-1 rounded-pill bg-white shadow-card">
                            {/* P1：不裸显 part_number，已有容量·颜色足够识别 */}
                            {p.capacity} · {p.color}
                          </span>
                        ))}
                    </div>
                  </Card>
                )}
                {/* P1：选了容量+颜色但商品编号未验证时，明确引导手动输入 */}
                {needsManualPn && (
                  <p className="mt-3 text-xs text-bad">
                    暂不支持此配置，请调整选择或手动填写商品编号。
                  </p>
                )}
                {selectedCaps.size > 0 && selectedColors.size > 0 && selectedParts.length === 0 && !needsManualPn && (
                  <p className="mt-3 text-xs text-bad">
                    所选容量与颜色没有对应配置，请调整选择
                  </p>
                )}
              </>
            )}
          </section>
        )}

        {/* Step 2: 选门店 */}
        {step === 2 && (
          <section>
            <SectionQ>你要监控哪些门店？</SectionQ>
            <SectionHint>已选 {pickedStores.size} 家门店</SectionHint>
            {canRefreshCatalog && (
              <button
                onClick={() => loadStores(1)}
                disabled={refreshing}
                className="text-xs text-accent font-medium disabled:opacity-40 mb-2"
              >
                {refreshing ? '刷新中…' : '在线刷新门店目录'}
              </button>
            )}
            {storeToast && (
              <p className="mb-2 rounded-card-sm bg-[#fff7e8] border border-[#f0c36d] px-3 py-2 text-xs text-[#b25e09]">
                {storeToast}
              </p>
            )}
            <input
              value={storeQuery}
              onChange={(e) => setStoreQuery(e.target.value)}
              placeholder="搜索城市 / 门店名 / 编号"
              className="w-full mb-3 px-4 py-3 rounded-card-sm bg-white shadow-card text-sm outline-none placeholder:text-faint"
            />
            {storeErr ? (
              <ErrorState message={storeErr} onRetry={() => loadStores()} />
            ) : stores === null ? (
              <LoadingState rows={3} />
            ) : (
              <div className="space-y-4">
                {storesByCity.map(([city, cityStores]) => {
                  const pickedInCity = cityStores.filter((s) => pickedStores.has(s.number)).length;
                  const allOn = pickedInCity === cityStores.length && cityStores.length > 0;
                  return (
                    <div key={city}>
                      <div className="flex items-center justify-between mb-2 px-1">
                        <span className="text-[13px] font-semibold">
                          {city}
                          <span className="mono font-normal text-faint ml-1.5">
                            {pickedInCity}/{cityStores.length}
                          </span>
                        </span>
                        <button
                          onClick={() => toggleCity(cityStores)}
                          className="text-[13px] text-accent font-medium active:scale-95 transition"
                        >
                          {allOn ? '取消全选' : '全选'}
                        </button>
                      </div>
                      <div className="space-y-2">
                        {cityStores.map((s) => {
                          const on = pickedStores.has(s.number);
                          return (
                            <button
                              key={s.number}
                              onClick={() => setPickedStores(toggle(pickedStores, s.number))}
                              aria-pressed={on}
                              className={`w-full text-left px-4 py-3.5 rounded-card-sm flex items-center justify-between transition active:scale-[0.99] border-2 ${
                                on ? 'bg-island text-white border-island' : 'bg-white border-transparent shadow-card'
                              }`}
                            >
                              <span className="text-[15px] font-medium truncate">
                                {s.name}
                                <span className={`mono text-xs ml-2 ${on ? 'text-white/60' : 'text-faint'}`}>
                                  {s.number}
                                </span>
                              </span>
                              <span
                                className={`w-6 h-6 rounded-full border-2 shrink-0 ml-2 flex items-center justify-center ${
                                  on ? 'bg-ok border-ok text-white text-sm' : 'border-line'
                                }`}
                              >
                                {on && '✓'}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
                {filteredStores.length === 0 && (
                  <EmptyState title="没有匹配的门店" hint="换个关键词试试" action={null} />
                )}
              </div>
            )}
          </section>
        )}

        {/* Step 3: 确认创建 */}
        {step === 3 && (
          <section className="space-y-5">
            {/* 汇总 */}
            <Card className="p-4 bg-bg">
              <p className="text-[13px] font-semibold mb-2">将创建 {combos} 个监控任务</p>
              <p className="text-xs text-sub leading-relaxed">
                {selectedParts.length} 种配置 × {pickedStores.size} 家门店
              </p>
              <p className="mt-1 text-xs text-sub">{selectedModel || '手动输入的商品'}</p>
              <details className="mt-2 text-xs text-sub"><summary className="cursor-pointer">核对配置与门店</summary>
                <p className="mt-2">{selectedParts.map((pn) => { const product = modelProducts.find((item) => item.part_number === pn); return product ? [product.capacity, product.color].filter(Boolean).join(' · ') : pn; }).join('、')}</p>
                <p className="mt-1">{(stores ?? []).filter((store) => pickedStores.has(store.number)).map((store) => store.name || store.number).join('、')}</p>
              </details>
              {(submitErr || overQuota || atQuota) && (
                <p role="alert" className="mt-2 text-sm text-bad">
                  {submitErr ||
                    (atQuota && combos === 0
                      ? `任务已达上限（${tasksLimit} 个），无法再创建`
                      : `将超出任务上限（${tasksLimit} 个）：已有 ${tasksUsed} 个，本次需生成 ${combos} 个`)}
                  {(showUpgradeLink || overQuota || atQuota) && (
                    <>
                      {' '}
                      <a href="/me" className="underline font-medium">
                        去开通会员
                      </a>
                    </>
                  )}
                </p>
              )}
            </Card>
            <div>
              <SectionQ>到货提醒</SectionQ>
              <div className="grid grid-cols-2 gap-2 mt-3">
                <button
                  onClick={() => setMode('instant')}
                  aria-pressed={mode === 'instant'}
                  className={`p-4 rounded-card-sm text-left transition active:scale-95 border-2 ${
                    mode === 'instant' ? 'bg-white border-accent shadow-card' : 'bg-white border-transparent shadow-card'
                  }`}
                >
                  <p className="text-[15px] font-semibold">到货即提醒</p>
                  <p className="mt-1 text-xs text-sub leading-relaxed">首次查到有货，或无货变有货时提醒。</p>
                </button>
                <button
                  onClick={() => setMode('confirmed')}
                  aria-pressed={mode === 'confirmed'}
                  className={`p-4 rounded-card-sm text-left transition active:scale-95 border-2 ${
                    mode === 'confirmed' ? 'bg-white border-accent shadow-card' : 'bg-white border-transparent shadow-card'
                  }`}
                >
                  <p className="text-[15px] font-semibold">确认后提醒</p>
                  <p className="mt-1 text-xs text-sub leading-relaxed">连续两次查到有货后提醒。</p>
                </button>
              </div>
              <label className="flex items-center gap-2 mt-4 text-sm">
                <input type="checkbox" checked={repeatEnabled} onChange={(e) => setRepeatEnabled(e.target.checked)} />
                持续有货时重复提醒
              </label>

              {repeatEnabled && (
                <input
                  type="number"
                  min={MIN_REPEAT_INTERVAL_SEC}
                  step={1}
                  aria-label="持续提醒间隔（秒）"
                  value={repeatInterval}
                  onChange={(e) => setRepeatInterval(e.target.value)}
                  inputMode="numeric"
                  placeholder="持续提醒间隔（秒，最小 60）"
                  className="mt-2 w-full px-3 py-2.5 rounded-card-sm bg-white shadow-card text-sm outline-none placeholder:text-faint mono"
                />
              )}
              <p className="mt-2 text-xs text-sub">{isAnon ? '体验任务可提醒 1 次。' : `剩余 ${quota ? Math.max(0, quota.push_limit - quota.push_used) : '—'} 次提醒。用完后自动暂停。`}</p>
              {showEstimate && (
                <details className="mt-2 text-xs text-sub"><summary className="cursor-pointer">重复提醒用量估算</summary><p className="mt-1">
                  持续有货 30 天约发送 <span className="mono text-ink font-medium">{monthlyEstimate}</span> 次
                  <span className="text-faint">（按检查间隔估算，实际按成功发送扣减）</span>
                </p></details>
              )}
            </div>

            <div>
              <div>
                <NotifyChannels value={channels} onChange={setChannels} />
              </div>
            </div>

            <details className="text-sm">
              <summary className="cursor-pointer">名称与分组（可选）</summary>
            <div>
              <label htmlFor="task-name" className="block text-sm font-medium mb-2">任务名（可选）</label>
              <input
                id="task-name" value={nameTemplate}
                onChange={(e) => setNameTemplate(e.target.value)}
                placeholder="如：iPhone 18 Pro Max × 万象城"
                className="w-full px-4 py-3 rounded-card-sm bg-white shadow-card text-sm outline-none placeholder:text-faint"
              />
            </div>

            <label className="block text-sm font-semibold">分组（可选）
              <input maxLength={128} value={groupName} onChange={(e) => setGroupName(e.target.value)} placeholder="如：给家人买手机" className="mt-2 w-full p-3 rounded-card-sm bg-white shadow-card font-normal" />
              <span className="block mt-1 text-xs text-sub font-normal">同一分组的任务可一起暂停、恢复或修改提醒。</span>
            </label>
            </details>

          </section>
        )}

        {step > 0 && step < 3 && (
          <Card className="mt-5 p-3.5" >
            <p className="text-sm font-medium" aria-live="polite">
              已选 {selectedParts.length} 种配置 × {pickedStores.size} 家门店 · 将创建 {combos} 个任务
            </p>
            <p className="mt-1 text-xs text-sub">
              {remainingTasks === null ? quotaError ? '任务名额暂未载入' : '正在获取任务名额' : `还可创建 ${remainingTasks} 个任务`}
            </p>
            {selectionOverQuota && (
              <p className="mt-2 text-xs text-bad">
                {isAnon ? '匿名体验限 1 种配置、1 家门店。' : '所选范围超出剩余名额，请减少选择。'}
                <a className="ml-1 underline" href={isAnon ? '/login' : '/me'}>{isAnon ? '注册 / 登录' : '查看会员权益'}</a>
              </p>
            )}
          </Card>
        )}
        {/* 导航按钮 */}
        <div className="fixed bottom-[calc(80px+env(safe-area-inset-bottom))] left-0 right-0 z-30 mx-auto max-w-lg px-4"><div className="flex gap-2.5 p-2 rounded-card bg-bg/95 shadow-card-lg">
          {step > 0 && (
            <button
              onClick={() => setStep(step - 1)}
              className="px-6 py-3.5 rounded-card-sm bg-white shadow-card text-[15px] font-medium text-sub transition active:scale-95"
            >
              上一步
            </button>
          )}
          {step < 3 ? (
            <button
              onClick={() => canNext() && setStep(step + 1)}
              disabled={!canNext()}
              className={`flex-1 py-3.5 rounded-card-sm text-[15px] font-semibold transition active:scale-[0.99] ${
                canNext() ? 'bg-accent text-white' : 'bg-line text-faint'
              }`}
            >
              {nextHint() ?? `下一步：${STEPS[step + 1]}`}
            </button>
          ) : (
            <PrimaryButton
              onClick={submit}
              disabled={submitting || overQuota || atQuota}
              className="flex-1"
            >
              {submitting
                ? '创建中…'
                : atQuota && combos === 0
                  ? `已达上限（${tasksLimit} 个）`
                  : overQuota
                    ? `超出上限（${tasksLimit} 个）`
                    : `创建 ${combos} 个监控任务`}
            </PrimaryButton>
          )}
        </div></div>
        {step === 0 && !isAnon && quota && quota.tasks_limit != null && (
          <p className="mt-3 text-xs text-sub text-center">
            {/* P1：说清名额口径——已用 X / 共 Y，避免"我第一次用怎么已经用了"的困惑 */}
            任务名额 <span className="mono text-ink font-medium">{quota.tasks_used ?? 0} / {quota.tasks_limit}</span>
            {combos > 0 && (
              <>（本次创建后 {(quota.tasks_used ?? 0) + combos} / {quota.tasks_limit}）</>
            )}
          </p>
        )}
      </div>
    </div>
  );
}
