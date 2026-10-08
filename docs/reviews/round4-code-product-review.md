# 第四轮代码审查 + 产品审查报告

- HEAD：`2c75c8f`（round3 修复后）
- 审查方式：四路并行（后端回归验证 / 后端找茬 / 前端+后台 / 部署+产品链路），全部读代码确认
- 结论：**未达到零问题**。P0 共 7 条（必须修），P1 共 22 条，P2 约 40 条（去重后）。

---

## P0（断裂：上线/部署前必须修）× 7

### R4-P0-1 · `backend/app/services/engine.py:823-836`：`_amain()` 不检查 `ENGINE_ENABLED`，手动启动即绕过开关
- 依据：`__main__` 直接 `asyncio.run(_amain())`，`_amain()` 直接 `await engine.start()`，整个 `engine.py` 无任何地方读取 `ENGINE_ENABLED`。
- 后果：任何人在 shell 里跑 `python -m app.services.engine`，无论开关何值引擎都会启动——双引擎重复轮询/重复通知的风险路径依然存在。
- 修复：在 `_amain` 开头加 fail-fast 检查。

### R4-P0-2 · `backend/alembic/env.py:38-46`：迁移连接无 WAL / busy_timeout
- 依据：`env.py` 用 `engine_from_config` 自建 engine，未注册任何 PRAGMA；`journal_mode=WAL` 是持久化设置（实际已生效），但 `busy_timeout` 是 per-connection。
- 后果：`deploy/deploy.sh:131` 在重启服务**之前**跑 `alembic upgrade head`，旧 engine/API 进程可能还持有写锁 → 迁移直接 `database is locked` 失败，部署中断。
- 修复：在 `run_migrations_online` 里对 connectable 加同样的 event 监听（或复用 `app.core.db` 的 engine）。

### R4-P0-3 · `backend/app/api/routers/admin.py:383-425`：`refund_payment` 未递减 `payments_amount_mismatch_pending`
- 依据：`refund_payment` 全程没有调用 `_dec_amount_mismatch_pending`；而 refund 只拦截 `p.status == "refunded"`（:396），`amount_mismatch → refunded` 是合法路径。
- 后果：金额异常订单被标记退款后，待处理计数虚高 1 且永不回落，后台"待处理"徽标长期误报。
- 修复：refund 成功后调用 `_dec_amount_mismatch_pending`。

### R4-P0-4 · `backend/app/services/engine.py:320-360`：预算跳过每 tick 写库，`notifications` 表无限膨胀
- 依据：`_apply_budgets` 里被预算跳过的任务调 `_record_skipped(..., channel="budget", ...)`，末尾 `if skipped: db.commit()`；但被跳过的任务不更新 `last_polled_at`，下个 tick 仍被判 due → 每 5 秒重复写行。
- 后果：pro 用户 30 任务超预算时 ≈ 360 行/分钟、50 万行/天；这些行还会污染 `GET /notifications` 列表和 `channels_health` 的 30 天全量加载。
- 修复：跳过只记内存计数 + 日志，不写 DB；或按 (task, 10分钟) 节流；最干净的是去掉该 `_record_skipped` 调用。

### R4-P0-5 · `backend/app/api/routers/catalog.py:172,177` vs `frontend/src/lib/api.ts:350` / `AddMonitor.tsx:65,106`：`refresh=1` 返回形状错位 → 管理员白屏
- 依据：refresh=1 成功时后端返回对象 `{"refreshing": True, "stores": [...]}`；前端 `api.stores()` 类型为 `StoreRef[]`，`stores.filter(...)` → `TypeError`，无 ErrorBoundary → 整页白屏。管理员登录前台点"在线刷新门店目录"必崩。
- 修复：后端 refresh=1 也返回纯数组（与 refresh=0 同形）；前端防御性解包 `data.stores ?? data`；契约文档补 refresh=1 返回形状。

### R4-P0-6 · `deploy/deploy.sh:130`：`source .env` 会被 `.env.example:25` 未加引号的 `SMTP_FROM` 炸掉
- 依据：`.env.example` 里 `SMTP_FROM=StockMon <noreply@glint.red>` 未加引号；运维照模板复制成 `.env` 后 `set -a; source` 报 `syntax error`（实测 exit=2），`set -e` 下 deploy.sh 在 migrate 步骤中止——此时 units/nginx/logrotate 已装好但代码未同步、服务未重启，处于"装了一半"状态。
- 修复：`.env.example:25` 加双引号；或 deploy.sh 用更安全的变量解析代替 `source`。

### R4-P0-7 · `deploy/deploy.sh:14-23`：prod 硬门槛零预检，重启后才暴露
- 依据：`main.py:44-60` 规定 prod 下 `AFDIAN_TOKEN/AFDIAN_PLAN_STANDARD/AFDIAN_PLAN_PRO/SMTP_HOST/SMTP_USER/SMTP_PASSWORD` 任一缺失直接 `RuntimeError` 拒绝启动；deploy.sh 只校验 `APP_SECRET_KEY`。
- 后果：部署到最后 `systemctl restart`，API 起不来，healthz 报 DEPLOY FAILED，服务停在坏状态（engine 因 Restart=always 空转、API 已死）。
- 修复：deploy.sh 的 `.env` 校验段加 prod 预检，缺失直接报错退出（早于任何重启）。

---

## P1（不自洽 / 高危）× 22

### 后端 × 8
- **R4-P1-B1** `auth.py:182`：邮箱大小写归一化不一致（仅 resend_code 做了 lower）→ `Foo@x.com` 与 `foo@x.com` 可注册成两个账号；换大小写登录 401。修复：register/login/verify/resend 四处统一 `strip().lower()`。
- **R4-P1-B2** `schemas.py:112-113` + `tasks.py:262`：`TaskBatchIn` 无上限笛卡尔积，物化后再做档位检查 → 1万×1万 OOM worker。修复：`max_length=30` 或 `len(combos) ≤ 900` 前置检查。
- **R4-P1-B3** `auth.py:205`：`login_failed` 日志打邮箱明文 → 撞库时攒出真实邮箱清单。修复：只记 ip/fails 或记哈希。
- **R4-P1-B4** `main.py:122-144`：每个 `/api` 请求同步写 `api_hits` 且无保留策略 → 表无限增长 + 与引擎争 SQLite 锁。修复：retention cron（删 90 天前）或采样。
- **R4-P1-B5** `tasks.py:172`：`list_tasks` N+1（`_task_out` 每任务一次 StockState 查询）。修复：`selectinload(MonitorTask.states)`。
- **R4-P1-B6** `tasks.py:258`：`batch_create` 不校验 category（`create_task` 校验）。修复：复用 `_validate_task_in`。
- **R4-P1-B7** 退款后 `user.quota_reset_at` 未重置 → 配额锚点仍是原购买日。修复：refund 时同步重置或按 free 口径重算。
- **R4-P1-B8** 退款把用户打回 free 后超限任务不暂停：`membership_sweep` 只处理 standard/pro 过期用户，tier 已是 free 直接跳过 → pro 的 30 个任务继续轮询、吃 free 的 5 次/月配额。修复：refund 后立即执行一次任务数收敛（复用 sweep 的 keep_limit 逻辑）。

### 前端 + 后台 × 7
- **R4-P1-F1** 后台"退款"无入口：`admin.py:349` 有 `/payments/{id}/refund`，`admin-api.ts` 无封装，付费页只有"认领"按钮。修复：加 `refundPayment(id)` + "标记退款"按钮 + 二次确认。
- **R4-P1-F2** 后台"关闭金额异常订单"无入口：`admin.py:344` 的 `/close` 无 UI。修复：加 `closePayment(id)` + "关闭（不处理）"按钮。
- **R4-P1-F3** 后台"手动标记邮箱已验证"无入口：`admin.py:64-66,211-213` 后端支持，`AdminUser` 类型无该字段（且 `list_users` 没返回它），会员页无按钮。修复：后端返回字段 + 会员表加列 + 按钮（后端已有审计）。
- **R4-P1-F4** 匿名 trial 链路前端不可达：`App.tsx:66-72` 未登录强制跳 login；`trialExhausted`（`Home.tsx:200-205`）条件恒 false → "体验推送已用完"横幅永不渲染。修复二选一：①前端实现匿名试用（localStorage device id + X-Device-Id）；②删横幅及"注册即开通体验会员"文案，文档明确仅登录链路。
- **R4-P1-F5** `History.tsx:226-263`：数据分析摘要渲染坏（`by_part`/`by_day` 数组 `String()` 成逗号垃圾文本，英文 key 裸奔）。修复：按契约渲染条形榜单 + key 映射中文。
- **R4-P1-F6** `stats.py:33`：`/stats/poll` 的 `last_poll_at` 缺 `Z` 后缀 → 前端按本地解析，北京用户"上次查询"晚 8 小时。修复：统一加 `Z`。
- **R4-P1-F7** `admin-api.ts:305`：`setPeakMode` 返回类型撒谎（声明 `SystemStatus`，实际 `{ok, peak_mode}`）。修复：改类型。

### 部署 + 链路 × 7
- **R4-P1-D1** `quota.py:16-40` / `lifecycle.py:129-141`：`pending_tier` 对用户完全不可见（`/quota`、`/me`、Me 页全无），且 sweep 晋升时不同步 `quota_reset_at` → 配额锚点与会员周期错位。修复：`/quota` 返回加字段 + Me 页展示"到期后切换" + sweep 同步锚点。
- **R4-P1-D2** `admin.py:163-169`：手动 `PATCH {"tier":"free"}` 只改 tier，不清空 `tier_expires_at`/`pending_tier` → 用户再买时 `apply_tier_grant` 取未来值白送天数；与 `/refund` 语义分叉（契约 82 行把两者都列为退款路径）。修复：tier 切 free 时同步清空。
- **R4-P1-D3** `admin.py:283-344`：`claim_payment` 内联授权直接 `user.tier = tier_to` 立即生效；webhook 的 `apply_tier_grant` 对降级购买走 `downgrade_pending`（到期生效）。管理员认领 standard 给 pro 在效期用户会立即降级，口径矛盾。修复：复用 `apply_tier_grant`。
- **R4-P1-D4** `deploy.sh:154-156`：健康检查形同虚设（healthz `status:ok` 硬编码；`sleep 3` 后引擎大概率还没写心跳）。修复：轮询最多 60s 等 `engine=="running"`。
- **R4-P1-D5** `pay.py:157-159`：未知 plan_id 回调直接 400 不落库 → 爱发电重试永远 400，已扣款订单在 Payment 表和后台都看不见。修复：落库 `status='unknown_plan'` 返回 200 + 待处理计数。
- **R4-P1-D6** `auth.py:166`：验证码过期文案"请重新注册获取" → 该邮箱已注册，重新注册必 400 `email_taken`，用户撞墙。修复：改为"请点击重新发送获取新验证码"。
- **R4-P1-D7** `backup.sh:24`：sqlite3 CLI 连接无 `busy_timeout`，3:10 可能与引擎写锁撞车 → 备份直接 FAIL 无告警。修复：加 `PRAGMA busy_timeout=15000`。

---

## P2（小问题）× 约40（分类汇总，明细见各路原始报告）

- **死代码**：`notifier.py:82` `CHANNEL_SENDERS` 零引用；`engine.py:190` `Engine.status()` 已死；`catalog.py:199` `seed_catalog` 零调用；`tasks.py:36` `HAS_AUTO_RETIRE_COL` 恒 True；`api.ts:299` `api.health` 零调用。
- **时区/口径**：`models.py:35` 注释称 quota_reset_at"北京时间口径"实际 UTC 锚点（注释撒谎）；`QuotaUsage.period` 列宽 7 实际写 10 字符（切 Postgres 会炸）；`admin.py:78` `today_pushes` 按 UTC 统计；`lifecycle.py:155,196` 通知日期 UTC 裸格式；Me 页"体验档同此口径"不实；配额预警文案"本月"与 30 天滚动矛盾；`/me` 与 `/quota` 的 `tasks_used` 口径不一致。
- **查询/性能**：`history.py:events` 无 limit（days≤365 全量进内存）；`analytics.py:_event_rows` 无界；`channels_health` 30 天全量进 Python（被 P0-4 放大）；`auth.py:_quota_for` 用 `len(list())` 而 `quota.py` 已用 `func.count()`；`_due_tasks` 每 5 秒全表扫。
- **边界/逻辑**：`renew_task` 提前续期丢剩余天数（应 `max(now, expires_at)+30d`）；`patch_task` 先 setattr 后校验；`classify` 缺字段判 unavailable 应判 unknown；`pay.py` 幂等非原子（并发重复回调 500）；`register` check-then-insert 竞态；`healthz` DB 挂仍 200；用户枚举（404 vs 400 可探测邮箱）；`notify.py:list_notifications` 返回 webhook URL/bark key 明文（建议前端脱敏）。
- **后台 UI**：时间全按 UTC 裸展示（与前台"本地时间"承诺矛盾）；前台付费记录不展示退款/异常状态；非管理员登录后台被静默踢回无提示；高峰模式开关无二次确认；总览页丢 `pending_payments` KPI；"累计收入 sub=税后口径"无依据；"六态"文案过时（实际 7 态）。
- **文档**：契约缺 `POST /auth/resend-code`、`GET /tasks/{id}`、`POST /tasks/{id}/renew`；运维手册仍是 round2 内容（无 ENGINE_ENABLED/sweep/WAL/refund 指引）；`history.py` 备注过时；`.env.example` 缺 `ENGINE_ENABLED` 本体；`ruff.toml` 在仓库根但 deploy 只同步 backend/；nginx 缺 `location = /api` 精确匹配；`Login.tsx:52-54` 重发 404 死代码。

---

## 回归验证通过的部分（第三轮修复真实生效）

- D1：API 进程开关 + systemd 显式 Environment + 三处 DB 心跳逻辑一致 ✓（仅 `_amain` 手动入口漏检查，见 P0-1）
- D2：应用内连接 WAL + busy_timeout 全覆盖 ✓（仅 alembic 连接漏，见 P0-2）
- D5：prod 启动门槛写法一致 ✓；手动验邮有审计日志 ✓（仅后台无入口，见 P1-F3）
- D3/D4：退款 status/revenue/tier/审计/计数递减链路通 ✓（仅 amount_mismatch 计数漏递减，见 P0-3）
- D7：降级先切换档位再取 tasks_limit ✓
- N8：sweep 降频生效、无竞态 ✓（漂移为已知取舍：60 tick × tick 时长，改 ENGINE_TICK_SEC 即漂移，未暴露为环境变量）
- Alembic 单头 ✓；round3 无 schema 变更、无需新迁移 ✓
- 失败≠无货铁律仍在 ✓；无静默吞异常 ✓；日志无密码/token ✓
- 前端：round3 宣称的 11 项修复全部真实生效（灵动岛跳转/403 保留列表/自动暂停卡片/续期阈值/展示态统一/验证码提示/付费记录/renew 返回/batch 字段/通知映射/页脚时区）
- 契约回归：单任务接口、通知 error 映射、resend-code、配额/榜单/健康接口全部对齐（除 P0-5）
- deploy：sweep 调度装得上、双 service 的 Environment 覆盖关系正确、备份覆盖 DB+.env ✓
- "预计送货日期"代码零残留，19 项清单文档同步 ✓

---

## 修复优先级建议

1. P0-6 + P0-7（部署脚本）：不修连部署都走不通
2. P0-4（notifications 膨胀）：上线后几天即爆表
3. P0-5（门店刷新白屏）：管理员必踩
4. P0-1 / P0-2 / P0-3
5. P1：支付链路先行（D1/D2/D3/D5/F1/F2/F3/B7/B8），再修体验类（F5/F6/B1/B2）
6. P2 批量修（死代码删除、文案统一、文档补齐）
