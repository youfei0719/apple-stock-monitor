# 第二轮产品逻辑审查 · G/H 维度（反薅与成本 / 前后台一致性）

## G. 反薅与成本

### 断裂-1：无任何全局/单用户 Apple 请求预算，一个重度用户可拉全站下水
- 代码依据：`config.py:68-71`（trial/free=300s、standard=30s、pro=10s）、`engine.py:187-196`（`_poll_group` 按 `APPLE_MAX_STORES_PER_REQ=10` 切 chunk，每 chunk 1 次 Apple 请求）、`engine.py:358-372`（`_enter_cooldown`：541/403 后 `wait=min(60*2^level,3600)`，最高 1 小时）、`engine.py:135-150`（tick 单线程串行，无并发上限；`ENGINE_MAX_WORKERS=4` 在 `config.py:64` 定义了但 engine.py 全文零引用）
- 场景复现：1 个 pro 用户批量建 30 个任务、每个盯不同城市的同一款 iPhone → 每 10 秒 30 次 Apple 请求（3 req/s）；10 个这样的用户 → 300 req/s → Apple 回 541 → `_enter_cooldown` 把本轮所有 due tasks（含别人的付费任务）的 `last_polled_at` 置为 now 且 `last_poll_ok=False`，全站进入最高 1 小时冷却。单个用户的成本被全站买单，含付费用户。
- 成熟产品做法：每用户每分钟 Apple 请求预算（如 pro ≤60 次/分）+ 全局令牌桶；超预算的任务本轮跳过并记 skipped，而不是触发全站熔断。
- 修复建议：`_due_tasks` 增加 per-user 本轮请求计数（内存 dict 即可），超限任务跳过；另加全局每分钟上限，超限时 pro 优先、trial/free 降速。

### 断裂-2：单任务门店数无上限，是成本放大器
- 代码依据：`schemas.py:58`（`TaskCreateIn.stores` 只有 `min_length=1`，无 `max_length`）；`engine.py:187-196`（1 个任务 N 个门店 = `ceil(N/10)` 次 Apple 请求）
- 场景复现：1 个免费用户建 3 个任务 × 每个塞 1000 个门店 = 每 300 秒 300 次 Apple 请求；pro 用户 30 任务 × 1000 门店 = 每 10 秒 3000 次 → 直接打爆 Apple 限流，触发断裂-1 的全站冷却。
- 修复建议：`stores` 加 `max_length`（如 20/任务）；engine 侧对超预算任务截断而非全量请求。

### 断裂-3：会员到期无人降级——付费到期 = 永久白嫖付费档
- 代码依据：`pay.py:124-126` 写入 `tier_expires_at`；`quota.py:31`、`admin.py:110` 只展示；全仓库无任何代码在到期后把 tier 降回 free（grep `tier_expires_at` 仅 5 处命中，无一处做降级）；引擎按 `t.user.tier` 调度（`engine.py:162`）
- 场景复现：用户付 ¥19 开通 standard，一个月后 `tier_expires_at` 过期，但 `tier` 字段仍是 `standard` → 引擎继续 30s 轮询 + 100 推送/月。前台 Me.tsx 会同时显示"已过期"的时间和 standard 档位权益，前后矛盾。
- 成熟产品做法：每日 cron 扫 `tier_expires_at < now AND tier != 'free'` → 降为 free + 记审计 + 通知用户。
- 修复建议：加 `tier-expiry-sweep` cron（或引擎 tick 内顺手处理）；直接丢钱，优先级最高。

### 断裂-4：匿名体验版可无限白嫖（换 IP + 换 device_id）
- 代码依据：`tasks.py:27`（`TRIAL_MAX_TTL=24h`）、`tasks.py:29-30,137-138`（创建任务限流 `20次/小时/IP`）、`tasks.py:119,132`（`device_id` 是客户端自报 `X-Device-Id` 头，可伪造）、`engine.py:311-328`（trial 配额记在 `system_config`，key=`trial_quota:<device_id>:<月份>`）
- 场景复现：换 IP + 随机 device_id → 每次都是全新 trial 身份：1 个任务、24h 存活、1 次推送/月。`20次/小时/IP` 对代理用户形同虚设。
- 更大洞：`auth.py:59-64` TODO 明说邮箱验证未实现，注册直接开通（`auth.py:70` 限 `5次/小时/IP`）→ 假邮箱 + 代理 = 无限免费账号（每号 3 任务 + 5 推送/月）。
- 成熟产品做法：邮箱验证必填；device 指纹 + IP 段联合限流。
- 修复建议：上线前必须补邮箱验证；trial 增加 IP 段维度限流可以晚点。

### 不自洽-1：`tiers.py` 的 channels 字段是装饰品，无任何强制
- 代码依据：`tiers.py` 声明 trial=`["page"]`、free/standard/pro=`["email"]`；但 `notifier.py:140-147` 的 `dispatch` 直接按任务 `channels` 字典发，**全仓库无一处校验 tier 通道**（grep `tier.*channels` 零命中）
- 场景：免费用户在任务里配 `bark_key` 照样收到 Bark 推送 → 付费墙漏了个洞。
- 修复建议：`dispatch` 入口处按 `tier_of(user.tier)["channels"]` 过滤，或任务创建/更新时校验 channels 合法性。

### 不自洽-2：`ENGINE_MAX_WORKERS=4` 是撒谎的配置
- 代码依据：`config.py:64` 定义，`engine.py` 零引用；实际轮询是 tick 内单线程 for 循环串行（`engine.py:135-150`）
- 影响：pro 的 10s SLA 在任务稍多时被静默打破——前台看到"刷新间隔 10s"（`quota.py:37` 返回配置值），实际可能几分钟才轮到。前台展示的是承诺，后端交付的是随缘。
- 修复建议：要么实现 worker 池，要么删掉该配置；任务量导致 tick 时长 > 最小间隔时记 warning。

### 对标差距（DING果）
1. 新品期免费用户禁查 / 高峰期降级：DING果有，我们完全没有——全站冷却纯被动且连付费用户一起停 → **必须补**（SystemConfig 手动开关：高峰模式下 trial/free 间隔拉长、catalog 后台刷新暂停、pro 优先）。
2. 3 秒刷新实时查询（会员特权）：我们无手动实时查询入口（只有后台目录刷新 `catalog.py:118`），pro 最高 10s 轮询 → **可以晚点**。
3. 通知按次消耗：**已对齐** ✓（`engine.py:290-309` 月配额 + 超额 `_record_skipped` 记 skipped）。
4. 完整历史数据（会员特权）：**已对齐** ✓（`history.py:68` `/releases` 按 `tier_of()["history"]` 门控，免费 403；`/events` 个人日志全开放，语义可接受）。

## H. 前后台一致性

### H1：tier 读取实时，前后台对得上——但"过期"语义是断的
- 对得上：引擎每 tick 直接读 `t.user.tier`（`engine.py:162`），无缓存 → 后台改档位 ≤5s 生效；前台 `/quota` 实时读库（`quota.py:17-38`）；Me.tsx 按实际字段渲染。✓
- 断裂：见 G 断裂-3。`tier_expires_at` 写了没人执行 → 后台/前台展示的"到期时间"和实际生效的档位是两套事实。

### H2：后台运营必需品清单
| 需求 | 状态 | 依据 |
|---|---|---|
| 封禁/解封用户 | 无 | `models.py:User` 无 banned 字段；`admin.py:125-149` 的 `patch_user` 只支持 tier/is_admin/paused_tasks |
| 手动补单（加会员） | 半有 | `patch_user` 可改 tier（`admin.py:131-134`），但 `AdminUserPatchIn`（`schemas.py:110-113`）无 `tier_expires_at` 字段 → 补的会员没有期限；手动加推送配额：无接口 |
| 全局公告/通知 | 无 | 无 notice 模型、无广播接口 |
| 手动触发任务轮询 | 无 | `admin.py` 只有 `/system` 只读状态 |
| 审计日志 | 有，真实 | `audit()` 在 `patch_user` 内调用（`admin.py:143-146`），后台 system 页可查；`GET /admin/audit`（`admin.py:224-258`） |

缺的四项中：封禁和手动补单（配额/期限）建议上线前补；全局公告、手动触发轮询可以晚点。

### H3：审计日志真实性
- 真实：`audit()` helper（`admin.py:38-52`）在 `patch_user` 中确实被调用，后台 system 页有展示区。不是摆设。
- 但单薄：后台唯一的写操作就是 `patch_user`，审计覆盖率 100% 只是因为写操作只有一个。随着封禁/补单等写操作补上，必须同步加 `audit()` 调用。

## 优先级排序
**必须补（上线前）**：1. 会员到期自动降级（G-断裂-3）2. 单任务门店数上限 + 单用户 Apple 请求预算（G-断裂-1/2）3. 邮箱验证（G-断裂-4 注册向量）4. 高峰模式开关（对标差距-1）
**可以晚点**：trial IP 段联合限流、后台封禁/补配额/公告/手动轮询、tier 通道强制校验、实时查询入口、删 `ENGINE_MAX_WORKERS` 撒谎配置。
**不用做**：通知按次消耗、历史数据门控——已对齐 DING果。
