import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError, cleanChannels, type Product, type Quota, type StoreRef, type Task, type TaskChannels } from '../lib/api';
import { useApp } from '../components/App';
import NotifyChannels from '../components/NotifyChannels';
import { Card, EmptyState, ErrorState, LoadingState, PageHeader, PrimaryButton } from '../components/ui';

/** 持续提醒间隔下限（秒）：后端 ge=3600 */
const MIN_REPEAT_INTERVAL_SEC = 3600;

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
  const { refreshTasks, me, tasks } = useApp();
  // UI-3：提交前按 tasks_limit 预检上限（登录用户拉 /quota；匿名按 trial 1 个算）
  const [quota, setQuota] = useState<Quota | null>(null);
  // R10-死代码4：UI-3 预检的 tasksUsed 回退不能用 tab 过滤后的 tasks（useApp 的 tasks
  // 随 Home tab 变化，低频不准）——这里单独拉一次全量计数
  const [allTaskCount, setAllTaskCount] = useState<number | null>(null);
  useEffect(() => {
    if (me) api.quota().then(setQuota).catch(() => setQuota(null));
    api
      .tasks('all')
      .then((t) => setAllTaskCount(t.length))
      .catch(() => setAllTaskCount(null));
  }, [me]);
  const isAnon = me === null;

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
  const [storeToast, setStoreToast] = useState<string | null>(null);

  const [nameTemplate, setNameTemplate] = useState('');
  const [group, setGroup] = useState('');
  const [mode, setMode] = useState<'instant' | 'confirmed'>('instant');
  // 连续确认模式下的重复提醒间隔（秒）；后端字段 repeat_interval_sec 已存在，batch 接口走 PATCH 补齐
  const [repeatInterval, setRepeatInterval] = useState('');
  const [channels, setChannels] = useState<TaskChannels>({});

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
    setStoreToast(null);
    if (refresh) setRefreshing(true);
    try {
      setStores(await api.stores(refresh));
    } catch (e) {
      // D10：403（门店目录刷新仅管理员可用）不清空已有列表，toast 提示即可；
      // R6-I14：429（refresh_limited）同样不清空列表，toast 提示即可
      if (e instanceof ApiError && (e.status === 403 || e.status === 429 || e.code === 'refresh_limited')) {
        if (refresh) {
          setStoreToast(
            e.status === 429 || e.code === 'refresh_limited'
              ? '刷新太频繁，请稍后再试'
              : '门店目录刷新仅管理员可用',
          );
        } else {
          setStoreErr('门店目录需要管理员权限，可直接手动填写门店编号');
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
    // R6-I3：脏 catalog 数据（缺 name/city/number）不再让 toLowerCase 崩掉渲染
    return stores.filter(
      (s) =>
        (s.name ?? '').toLowerCase().includes(q) ||
        (s.city ?? '').toLowerCase().includes(q) ||
        (s.number ?? '').toLowerCase().includes(q),
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
    // 持续提醒间隔下限 3600 秒（与后端 ge=3600 对齐）
    const ri = parseInt(repeatInterval, 10);
    if (mode === 'confirmed' && repeatInterval.trim()) {
      if (!Number.isFinite(ri) || ri < MIN_REPEAT_INTERVAL_SEC) {
        setSubmitErr(`持续提醒间隔至少 ${MIN_REPEAT_INTERVAL_SEC} 秒（1 小时），防止一夜烧完周期配额`);
        return;
      }
    }
    // UI-3：提交前先按 tasks_limit 提示上限，别等提交时吃 403
    const combos = partNumbers.length * store_numbers.length;
    const tasksLimit = isAnon ? 1 : quota?.tasks_limit;
    // R10-死代码4：回退用全量计数（allTaskCount），不用 tab 过滤后的 tasks.length
    const tasksUsed = isAnon
      ? (allTaskCount ?? tasks.length)
      : (quota?.tasks_used ?? allTaskCount ?? tasks.length);
    if (combos > 0 && tasksLimit !== undefined && tasksUsed + combos > tasksLimit) {
      setSubmitErr(
        `将超出任务上限（${tasksLimit} 个）：已有 ${tasksUsed} 个，本次需生成 ${combos} 个，请减少机型或门店选择`,
      );
      return;
    }
    setSubmitting(true);
    try {
      // F-1：登录用户走批量接口；匿名用户走单任务接口逐个创建
      // （后端 /tasks/batch 要求登录，匿名调 batch 会 401）
      const nameTpl = nameTemplate.trim() || '{part_number} × {store_number}';
      const repeatSec = mode === 'confirmed' && repeatInterval.trim() && Number.isFinite(ri) ? ri : null;
      // R10-I4：空 webhook URL 后端会 422（min_length=1），提前过滤掉未填写的行
      // （与 TaskDetail.saveChannels 共用 lib.cleanChannels）
      const clean = cleanChannels(channels);
      if (isAnon) {
        const storeByNumber = new Map((stores ?? []).map((s) => [s.number, s]));
        const productByPn = new Map((products ?? []).map((p) => [p.part_number, p]));
        let createdCount = 0;
        try {
          for (const pn of partNumbers) {
            for (const sn of store_numbers) {
              const prod = productByPn.get(pn);
              const info = storeByNumber.get(sn);
              await api.createTask({
                name: nameTpl.replace('{part_number}', pn).replace('{store_number}', sn),
                group: group.trim(),
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
            }
          }
        } catch (e) {
          if (createdCount > 0) await refreshTasks('active');
          const msg = e instanceof Error ? e.message : '创建失败';
          setSubmitErr(
            createdCount > 0 ? `已创建 ${createdCount} 个任务，后续创建失败：${msg}` : msg,
          );
          return;
        }
      } else {
        // batch 接口直接接受 category/mode/channels；group/repeat_interval_sec 仍走 PATCH 补齐
        const created = await api.batchTasks({
          part_numbers: partNumbers,
          store_numbers,
          // 后端只替换 {part_number} / {store_number}，模板必须用这两个占位符
          name_template: nameTpl,
          category,
          mode,
          channels: clean,
        });
        const patch: Partial<Task> = {};
        if (group.trim()) patch.group = group.trim();
        if (repeatSec !== null) patch.repeat_interval_sec = repeatSec;
        if (Object.keys(patch).length > 0) {
          // R9-D4：PATCH 补齐阶段单独捕获——batch 已成功（任务已入库），
          // 任一 PATCH 失败不能渲染成"创建失败"，否则用户重试会撞后端 409 查重；
          // 照常 refreshTasks + 跳首页，失败信息经首页横幅（location.state.notice）展示一次
          try {
            await Promise.all(created.map((t) => api.updateTask(t.id, patch)));
          } catch (patchErr) {
            try {
              await refreshTasks('active');
            } catch {
              /* 刷新失败不影响：任务已创建，首页会自行重试加载 */
            }
            navigate('/', {
              state: {
                notice: `任务已创建，但分组/重复间隔设置失败：${patchErr instanceof Error ? patchErr.message : '未知错误'}`,
              },
            });
            return;
          }
        }
      }
      await refreshTasks('active');
      navigate('/');
    } catch (e) {
      setSubmitErr(e instanceof Error ? e.message : '创建失败');
    } finally {
      setSubmitting(false);
    }
  };

  // 预计月消耗（上限估算，按成功发送计）：ceil(30*24*3600 / interval) × 门店数
  const riNum = parseInt(repeatInterval, 10);
  const showEstimate =
    mode === 'confirmed' && Number.isFinite(riNum) && riNum >= MIN_REPEAT_INTERVAL_SEC;
  const monthlyEstimate = showEstimate
    ? Math.ceil((30 * 24 * 3600) / riNum) * Math.max(1, pickedStores.size)
    : 0;

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
                        {/* R6-I3：缺 price_cny 的脏条目回退 0，避免 toLocaleString 崩溃 */}
                        ¥{(p.price_cny ?? 0).toLocaleString('zh-CN')}
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
          {storeToast && (
            <p className="mb-2 rounded-card-sm bg-[#fff7e8] border border-[#f0c36d] px-3 py-2 text-xs text-[#b25e09]">
              {storeToast}
            </p>
          )}
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
              placeholder="任务命名模板，如：{part_number} × {store_number}"
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
            {mode === 'confirmed' && (
              <>
                <input
                  value={repeatInterval}
                  onChange={(e) => setRepeatInterval(e.target.value)}
                  inputMode="numeric"
                  placeholder="持续提醒间隔（秒，最小 3600）"
                  className="w-full px-3 py-2.5 rounded-card-sm bg-bg text-sm outline-none placeholder:text-faint mono"
                />
                {showEstimate && (
                  <p className="text-xs text-sub leading-relaxed">
                    预计月消耗 ≈ <span className="mono text-ink font-medium">{monthlyEstimate}</span>{' '}
                    次
                    <span className="text-faint">（上限估算，按实际发送成功的通知条数计）</span>
                  </p>
                )}
                <p className="text-[11px] text-faint leading-relaxed">
                  持续有货时每隔该时间再提醒一次；间隔至少 1 小时，防止快速烧完周期配额。
                </p>
              </>
            )}
          </Card>
        </section>

        {/* 通知渠道（与任务详情页同一套表单） */}
        <section>
          <NotifyChannels value={channels} onChange={setChannels} />
        </section>

        {submitErr && (
          <p className="text-sm text-bad text-center">{submitErr}</p>
        )}

        <PrimaryButton onClick={submit} disabled={submitting}>
          {submitting
            ? '生成中…'
            : isAnon
              ? `创建 ${partNumbers.length * pickedStores.size} 个监控任务`
              : `批量生成 ${partNumbers.length} 机型 × ${pickedStores.size} 门店`}
        </PrimaryButton>
        <p className="text-center text-xs text-faint">
          共生成 <span className="mono">{partNumbers.length * pickedStores.size}</span> 个监控组合
        </p>
      </div>
    </div>
  );
}
