import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api, ApiError, cleanChannels, isWaitingForLaunch, launchTimeLabel, type Product, type Quota, type StoreRef, type TaskChannels } from '../lib/api';
import { configurationLabel, productTitle, selectCatalogParts } from '../lib/catalog';
import { useApp } from '../components/App';
import NotifyChannels from '../components/NotifyChannels';
import { Card, EmptyState, ErrorState, LoadingState, PageHeader, PrimaryButton } from '../components/ui';

/** 与 TaskCreateIn / TaskBatchIn 的 ge=60 对齐。 */
const MIN_REPEAT_INTERVAL_SEC = 60;

const CATEGORY_LABELS: Record<string, string> = {
  iphone: 'iPhone', ipad: 'iPad', mac: 'Mac', watch: 'Apple Watch',
  airpods: 'AirPods', homepod: 'HomePod', airtag: 'AirTag', display: '显示器', vision: 'Vision Pro',
};

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
  const [searchParams] = useSearchParams();
  const initialModel = searchParams.get('model');
  const previousCategory = useRef<string | null>(null);
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
  const [categories, setCategories] = useState<{ id: string; label: string; sku_count: number }[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const productLoadRef = useRef(0);
  const loadCategories = async () => {
    setCatalogError(null);
    try { setCategories((await api.catalogCategories()).categories.filter((c) => c.sku_count > 0)); }
    catch (error) { setCatalogError(error instanceof Error ? error.message : '品类载入失败'); }
  };
  useEffect(() => { void loadCategories(); }, []);
  const [products, setProducts] = useState<Product[] | null>(null);
  const [prodErr, setProdErr] = useState<string | null>(null);

  // Step 1: 选机型（按产品名分组）
  const [selectedModel, setSelectedModel] = useState<string | null>(initialModel);

  // Step 2: 选配置（容量/颜色多选）
  const [selectedCaps, setSelectedCaps] = useState<Set<string>>(new Set());
  const [selectedColors, setSelectedColors] = useState<Set<string>>(new Set());
  const [selectedDetails, setSelectedDetails] = useState<Record<string, Set<string>>>({});
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
    const generation = ++productLoadRef.current;
    setProdErr(null);
    setProducts(null);
    try {
      const result = await api.products(category);
      if (generation === productLoadRef.current) setProducts(result.filter((p) => p.monitorable !== false));
    } catch (e) {
      if (generation === productLoadRef.current) setProdErr(e instanceof Error ? e.message : '加载失败');
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
    if (previousCategory.current !== null && previousCategory.current !== category) {
      setSelectedModel(null);
      setSelectedCaps(new Set());
      setSelectedColors(new Set());
      setSelectedDetails({});
      setExpert(false);
      setExpertText('');
    }
    previousCategory.current = category;
    void loadProducts();
    return () => { productLoadRef.current++; };
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
      launch: items.find((p) => isWaitingForLaunch(p))?.launch,
      minPrice: Math.min(...items.map((i) => i.price_cny ?? 0)),
    }));
  }, [products]);

  // 选中机型的所有变体（Step 2 用）
  const modelProducts = useMemo(() => {
    if (!selectedModel) return [];
    return (products ?? []).filter((p) => p.name === selectedModel);
  }, [products, selectedModel]);

  const selectedLaunch = modelProducts.find((p) => isWaitingForLaunch(p))?.launch;
  const needsPrelaunchLogin = isAnon && Boolean(selectedLaunch);
  const loginForLaunch = () => navigate('/login', { state: { returnTo: `/add?model=${encodeURIComponent(selectedModel ?? '')}` } });

  const capacities = useMemo(() => {
    const set = new Set<string>();
    modelProducts.forEach((p) => p.capacity && set.add(p.capacity));
    const size = (value: string) => {
      const match = value.match(/^([\d.]+)\s*(GB|TB)$/i);
      return match ? Number(match[1]) * (match[2].toUpperCase() === 'TB' ? 1024 : 1) : 0;
    };
    return [...set].sort((a, b) => size(a) - size(b));
  }, [modelProducts]);

  const colors = useMemo(() => {
    const set = new Set<string>();
    modelProducts.forEach((p) => p.color && set.add(p.color));
    return [...set];
  }, [modelProducts]);

  // Only axes present in actual Apple rows are exposed; fixed details remain visible.
  const detailDimensions = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const product of modelProducts) {
      for (const [key, value] of Object.entries(product.attributes ?? {})) {
        if (['存储容量', '颜色', '屏幕尺寸'].includes(key) || !value) continue;
        if (!map.has(key)) map.set(key, new Set());
        map.get(key)!.add(value);
      }
    }
    return [...map].map(([key, values]) => ({ key, values: [...values] }));
  }, [modelProducts]);

  const selectedParts = useMemo(() => {
    if (expert) return [...new Set(expertText.split(/[\s,，;；\n]+/).map((s) => s.trim().toUpperCase()).filter(Boolean))];
    const filters: Record<string, Set<string>> = {};
    if (capacities.length) filters['存储容量'] = selectedCaps;
    if (colors.length) filters['颜色'] = selectedColors;
    for (const dimension of detailDimensions) {
      if (dimension.values.length > 1) filters[dimension.key] = selectedDetails[dimension.key] ?? new Set();
    }
    return selectCatalogParts(modelProducts, filters);
  }, [expert, expertText, modelProducts, selectedCaps, selectedColors, capacities, colors, detailDimensions, selectedDetails]);

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
    if (step === 1 && selectedParts.length === 0) return '请选择具体配置';
    if (step === 1 && selectionOverQuota) return '请减少配置或增加任务名额';
    if (step === 2 && overQuota) return '请减少门店或增加任务名额';
    if (step === 2 && pickedStores.size === 0) return '选至少一家门店';
    return null;
  };

  const submit = async () => {
    setSubmitErr(null);
    if (isAnon && (needsPrelaunchLogin || (products ?? []).some((p) => selectedParts.includes(p.part_number) && isWaitingForLaunch(p)))) {
      loginForLaunch();
      return;
    }
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
              const partName = prod ? productTitle(prod) : pn;
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
                product_name: prod ? productTitle(prod) : '',
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
          : selectedLaunch ? `已预约 ${doneCount} 个监控任务，开售后自动检查所选门店` : `已创建 ${doneCount} 个监控任务` },
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
      <PageHeader title="添加监控" />
      <div ref={stepPanel} tabIndex={-1} aria-label={STEPS[step]} className="px-4 pb-28 outline-none">
        <Stepper step={step} />
        {quotaError && <div role="alert" className="mb-3 text-sm text-bad">额度载入失败，创建时会再次校验。<button onClick={() => void loadQuota()} className="ml-2 text-accent">重试</button></div>}

        {/* Step 0: 选机型 */}
        {step === 0 && (
          <section>
            <SectionQ>你要等哪款 {CATEGORY_LABELS[category] ?? '产品'} 补货？</SectionQ>
            <SectionHint>收录需要等补货的重点款，也支持提前预约新品监控。</SectionHint>
            <div className="flex flex-wrap gap-2 mb-4">
              {categories.map((c) => (
                <button
                  key={c.id}
                  onClick={() => setCategory(c.id)}
                  className={`px-3 py-2 rounded-card-sm text-sm font-medium transition active:scale-95 flex flex-col items-center justify-center gap-0.5 ${
                    category === c.id ? 'bg-island text-white' : 'bg-white text-sub shadow-card'
                  }`}
                >
                  <span>{c.label}</span>
                </button>
              ))}
            </div>
            {/* P1：品类不可选的原因说清楚，不只写"暂未支持"四个字 */}

            {catalogError && <ErrorState message={catalogError} onRetry={loadCategories} />}
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
                      onClick={() => {
                        if (selectedModel !== g.name) {
                          setSelectedCaps(new Set()); setSelectedColors(new Set()); setSelectedDetails({});
                          setExpert(false); setExpertText('');
                        }
                        setSelectedModel(g.name);
                      }}
                      aria-pressed={on}
                      className={`w-full text-left p-4 rounded-card transition active:scale-[0.99] border-2 ${
                        on ? 'bg-white border-accent shadow-card' : 'bg-white border-transparent shadow-card'
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-[17px] font-semibold product-name">{g.name}</span>
                        {g.launch && <span className="ml-auto mr-2 text-xs text-accent">可提前预约</span>}
                        {on && <span className="text-accent text-lg">✓</span>}
                      </div>
                      <p className="mt-1 text-[13px] text-sub">
                        {g.count} 种{g.launch ? '预约' : '补货'}配置{g.unavailable > 0 && <> · {g.unavailable} 种待补齐</>} · <span className="mono">¥{g.minPrice.toLocaleString('zh-CN')} 起</span>
                      </p>
                    </button>
                  );
                })}
              </div>
            )}
            {needsPrelaunchLogin && <p className="mt-4 text-sm text-sub">预约监控需登录保存，开售前不会过期。登录后继续选择配置与门店。</p>}
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
            <SectionHint>{selectedModel} · 请选择具体配置，可多选</SectionHint>
            {selectedLaunch ? <Card className="p-4 mb-4 text-sm">
              <p className="font-semibold text-accent">可提前预约 · 等待开售</p>
              <p className="mt-2">预计 {launchTimeLabel(selectedLaunch.preorder_at)} 开放预购</p>
              <p className="mt-2 text-xs text-sub">先保存配置和门店，开售后自动检查所选门店，确认可购买才提醒。{selectedLaunch.note}</p>
            </Card> : <p className="mb-3 text-xs text-sub">这些配置在近期核验的门店出现缺货；各城市供应不同，创建后会检查你选的门店。</p>}
            {category === 'watch' && <p className="mb-3 text-xs text-sub">监控表壳库存；表带需在 Apple 购买时另外选择，表壳有货不代表整套组合有货。</p>}
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
                    setSelectedDetails(Object.fromEntries(detailDimensions.map((d) => [d.key, new Set(d.values)])));
                    setSelectedColors(new Set(modelProducts.filter((p) => p.part_number).map((p) => p.color)));
                  }}>全选可添加配置</button>
                  <button onClick={() => {
                    setSelectedCaps(new Set());
                    setSelectedColors(new Set());
                    setSelectedDetails({});
                  }}>清空选择</button>
                </div>
                {capacities.length > 0 && (
                  <>
                    <p className="text-[13px] font-semibold text-sub mb-2 mt-4">容量</p>
                    <div className="flex flex-wrap gap-2">
                      {capacities.map((c) => {
                        const on = selectedCaps.has(c);
                        const prices = modelProducts.filter((p) => p.capacity === c).map((p) => p.price_cny);
                        const price = prices.length ? Math.min(...prices) : null;
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
                                ¥{price.toLocaleString('zh-CN')}{prices.some((p) => p !== price) ? ' 起' : ''}
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
                {detailDimensions.map((dimension) => (
                  <fieldset key={dimension.key} className="mt-4">
                    <legend className="text-[13px] font-semibold text-sub mb-2">{dimension.key}</legend>
                    <div className="flex flex-wrap gap-2">
                      {dimension.values.map((value) => {
                        const fixed = dimension.values.length === 1;
                        const on = fixed || selectedDetails[dimension.key]?.has(value) === true;
                        return <button key={value} type="button" aria-pressed={on} disabled={fixed}
                          onClick={() => setSelectedDetails((current) => ({ ...current, [dimension.key]: toggle(current[dimension.key] ?? new Set(), value) }))}
                          className={`px-3 py-2.5 rounded-card-sm text-sm border-2 ${on ? 'bg-island text-white border-island' : 'bg-white text-sub border-transparent shadow-card'}`}>
                          {value}
                        </button>;
                      })}
                    </div>
                  </fieldset>
                ))}
                <details className="mt-4 text-xs text-sub">
                  <summary className="cursor-pointer">查看支持的配置</summary>
                  <ul className="mt-2 space-y-1 text-xs">
                    {modelProducts.map((p) => <li key={p.part_number} className="flex justify-between gap-2">
                      <span>{configurationLabel(p)}<span className="block mono text-[10px] text-faint">{p.part_number}{p.model_number ? ` · 型号 ${p.model_number}` : ''}</span></span>
                      <span className={p.part_number ? 'text-ink' : 'text-sub'}>{p.part_number ? '可添加' : '待补齐'}</span>
                    </li>)}
                  </ul>
                  {selectedCaps.size > 0 && selectedColors.size > 0 && <p className="mt-2 text-xs text-sub">
                    已选 {selectedParts.length} 种配置；未收录编号的配置暂不能添加。
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
                            {configurationLabel(p)}
                          </span>
                        ))}
                    </div>
                  </Card>
                )}
                {selectedParts.length === 0 && (
                  <p className="mt-3 text-xs text-sub">请完成各项配置选择；只有官网实际存在的 SKU 才能添加。</p>
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
              <p className="text-[13px] font-semibold mb-2">将{selectedLaunch ? '预约' : '创建'} {combos} 个监控任务</p>
              <p className="text-xs text-sub leading-relaxed">
                {selectedParts.length} 种配置 × {pickedStores.size} 家门店
              </p>
              <p className="mt-1 text-xs text-sub">{selectedModel || '手动输入的商品'}</p>
              {selectedLaunch && <p className="mt-2 text-xs text-accent">保存后等待开售，预计 {launchTimeLabel(selectedLaunch.preorder_at)} 起检查所选门店。确认可购买后按下方设置提醒。</p>}
              <details className="mt-2 text-xs text-sub"><summary className="cursor-pointer">核对配置与门店</summary>
                <p className="mt-2">{selectedParts.map((pn) => { const product = modelProducts.find((item) => item.part_number === pn); return product ? configurationLabel(product) : pn; }).join('、')}</p>
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
              onClick={() => { if (!canNext()) return; if (step === 0 && needsPrelaunchLogin) loginForLaunch(); else setStep(step + 1); }}
              disabled={!canNext()}
              className={`flex-1 py-3.5 rounded-card-sm text-[15px] font-semibold transition active:scale-[0.99] ${
                canNext() ? 'bg-accent text-white' : 'bg-line text-faint'
              }`}
            >
              {step === 0 && needsPrelaunchLogin ? '登录后预约监控' : nextHint() ?? `下一步：${STEPS[step + 1]}`}
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
                    : `${selectedLaunch ? '预约' : '创建'} ${combos} 个监控任务`}
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
