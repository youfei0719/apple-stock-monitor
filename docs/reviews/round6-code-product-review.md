# 第六轮代码审查 + 产品审查报告（HEAD=1623e28）

- 审查方式：四路并行（后端回归验证 / 后端找茬 / 前端+后台 / 部署+文档），全部读代码确认，只读未改代码
- 结论：**未达到零问题**。回归验证四路全部通过（无回归失败），新挖出 **8 条断裂、14 条不自洽、4 个 UI 问题、22 个 P2**（已去重）。

## 回归验证（全部通过，无回归失败）

- 部署+文档路：D-1~D-5、D-N1~D-N8、P0-6/P0-7 共 15 项全过（含本地实测行尾注释提取、export 前缀、certonly 顺序）
- 前端+后台路：F-1/F-2/F-3、F-N1~F-N8、UI-1~UI-5、N14（B 方案）共 17 项全过
- 后端回归路：F-4（page 触达计 sent=1、按 sent 扣减、匿名 /quota、横幅无死代码）、B-1~B-4、B-N1~B-N5、竞态-1/2、N14 共 9 项全过；后端测试 54 个全过；Alembic 单头
- 19 项功能清单抽查：代理池真轮换、Bark、part number 专家模式、链路测试、放货记录/榜单/指南均有实现；"预计送货日期"代码零残留

---

## 一、断裂（8 条）

### R6-D1 · 限流后同 tick 其余分组继续打 Apple，退避被架空
- `backend/app/services/engine.py:233`（tick 循环）+ `:380-386`（`_poll_group` 的 `except AppleRateLimitError`）
- 依据：`_poll_group` 捕获限流后 `return` 只退出当前分组，tick 循环继续下一个分组再次 `query(...)`；`_in_cooldown` 在 tick 内只检查一次（:228）。一次 429 后剩下所有分组照打，每个再吃 429，`_enter_cooldown` 的 `wait = 60 * 2^level` 被连踩多级直顶 3600s。
- 修复：`_poll_group` 返回是否被限流，或 tick 每次迭代前重查 `_in_cooldown`；注释改准确。

### R6-D2 · 并发不同订单 webhook → 会员天数 lost-update（付两次钱只加 30 天）
- `backend/app/api/routers/pay.py:82-115`（`apply_tier_grant`）
- 依据：`base = tier_expires_at if > now else now` → `tier_expires_at = base + 30d` 是 read-modify-write；`_add_payment_atomic` 只防同 order_id，不防两个不同 order_id 并发（连买两次/续费+升级）：两请求读到同一 base，各自写回 `base+30d`，60 天变 30 天。单 worker 也 race（异步 handler 并发 + SQLite 读-改-写跨事务）。
- 修复：原子 SQL：`UPDATE users SET tier_expires_at = datetime(max(tier_expires_at, :now), '+30 days') WHERE id=:id`，或同一事务内重读。

### R6-D3 · `_bootstrap_admin` 建出的 pro 管理员 5 分钟内被 sweep 降回 free
- `backend/app/main.py:76-100` vs `backend/app/services/lifecycle.py:191-200`
- 依据：bootstrap 建 `User(tier="pro")`，`tier_expires_at=None`；sweep 的 R5-B-N1 查询是 `tier.in_(["standard","pro"])` 且 `(tier_expires_at < now OR tier_expires_at IS NULL)`——管理员精确命中 NULL 回收分支，被原子 UPDATE 降为 free，还附赠 `membership_changed` 通知 + `converge_task_limit` 按 free 暂停他的任务。
- 修复：bootstrap 时 `tier_expires_at` 设远未来值（如 +10 年），或 sweep 查询加 `User.is_admin.is_(False)`。

### R6-D4 · 渠道配空的付费任务：到货边沿被静默消费，零通知、零记录
- `backend/app/services/engine.py:423-427` + `backend/app/services/notifier.py:117-` + `backend/app/schemas.py`
- 依据：`channels={}` 时 `dispatch` 的 jobs 为空、非 trial 故 `page_only=False` → 返回 `[]`；`_fire` 里 `sent=0`，`if records:` 跳过；`_process_task` 里 `_consume_quota(0)` 直接返回 → `row.last_event_at = now` 照写。边沿被消费，notifications 表零痕迹，用户永远收不到且系统无提示。`TaskCreateIn.channels` 默认全空，创建/PATCH 都不拦。同类：档位不支持的通道全 skipped 时同样推进 `last_event_at`（事件永久丢失）。
- 修复：创建/PATCH 时若 `effective_tier != trial` 且 channels 全空则 400；或引擎遇到零渠道/全 skipped 时记一条 `skipped` 通知并**不**推进 `last_event_at`。

### R6-D5 · 任务列表加载失败被误导成"还没有监控任务"
- `frontend/src/components/App.tsx:37-44` + `frontend/src/pages/Home.tsx:44-56`
- 依据：`refreshTasks` 内部吞掉所有异常永不 throw，Home 的 `reload()` 的 catch（含 `<ErrorState>`）永不可达。已登录用户 GET /tasks 失败（500/网络）时，loading 后 `tasks=[]` 渲染"还没有监控任务"+添加按钮，用户以为任务丢了甚至重复创建。
- 修复：`refreshTasks` 把异常抛出来（或返回结果），或在 App 层维护 `tasksError` 状态。

### R6-D6 · 全站无 ErrorBoundary，任何渲染期异常直接白屏
- `frontend/src/main.tsx`
- 依据：无错误边界；结合 R6-I3（脏 catalog 数据），一次脏数据即整页白屏无提示。
- 修复：在 App 外层加 ErrorBoundary（出错页+重试，与现有 `ErrorState` 风格统一）。

### R6-D7 · certbot 自动续期后 nginx 不 reload，续期等于白续
- `deploy/deploy.sh:167`
- 依据：`certbot certonly ... --standalone -d` 未加 `--deploy-hook`；certonly 无 installer，timer 自动续期只换文件不 reload nginx，常驻进程继续用内存旧证书，60–90 天后对外提供过期证书 → HTTPS 中断。运维手册写"后续续期走自动续期无需人工"——自相矛盾。
- 修复：追加 `--deploy-hook "systemctl reload nginx"`（首次申请触发一次 reload，无害且幂等）。

### R6-D8 · 全新 Ubuntu 上 node 版本未校验，vite 6 构建必挂
- `deploy/deploy.sh` 前置检查
- 依据：只查 node/npm 是否存在；Ubuntu 22.04 apt 的 nodejs 是 v12.x，而前后端都是 vite 6（要求 Node ≥18）。`npm ci` 能过，`npm run build`（`tsc && vite build`）在 `set -e` 下中断——首次部署无前端可用，重部署停在"后端已更新、前端构建失败"中间态。
- 修复：前置检查加 `node -v` 主版本号 ≥18 校验 + nodesource 安装指引，或文档限定 Ubuntu 24.04+。

---

## 二、不自洽（14 条）

### R6-I1 · renew 语义契约与实现相反
- `docs/API_CONTRACT.md:111` vs `backend/app/api/routers/tasks.py:357-376`
- 依据：契约写"提前续期**不保留**剩余天数（从现在起 30 天）"；实现（Round4 改的）是 `max(now, expires_at) + 30天`，即**保留**。Round4 改实现漏改契约。
- 修复：改契约（推荐，保留剩余天数更合理）或改实现，二选一。

### R6-I2 · 匿名 trial 配额分支在契约中完全未定义
- `backend/app/api/routers/quota.py:28-51` vs `docs/API_CONTRACT.md`
- 依据：F-4 新增：未登录 + `X-Device-Id` 返回 `{tier:"trial", quota_reset_at:null, period:"YYYY-MM"}`（自然月）；契约配额节只定义登录用户语义（购买日+30天滚动）。实现自洽，纯文档缺口。
- 修复：契约配额节补 trial 匿名配额说明。

### R6-I3 · 脏 catalog 数据可致渲染崩溃（高危，与 R6-D6 联动）
- `frontend/src/pages/AddMonitor.tsx:307`（`p.price_cny.toLocaleString`）、`:114-115`（`s.city.toLowerCase()`）
- 依据：后端 `/catalog/products` 直接返回 `system_config` 原始 dict，不做 schema 校验；一条缺字段的产品即 TypeError 白屏（无 ErrorBoundary 放大）。
- 修复：渲染前过滤/回退（`p.price_cny ?? 0`、`(s.city ?? '')`），或后端加 schema 校验。

### R6-I4 · `_claim_device_tasks` 信任客户端 `X-Device-Id` 做任务过户（安全高危）
- `backend/app/api/routers/auth.py:103-125`
- 依据：登录/注册时按请求头 `x-device-id` 把匿名任务批量改绑到登录用户，并过户历史通知；任务 `channels` 含 bark_key、webhook URL、email。`X-Device-Id` 客户端自选、无持有证明；知道/撞中 device_id 即可把别人的试用任务连同通知密钥过户到自己名下。"凭可伪造 header 转移密钥资产"是越权原语（device_id 为 UUID 时利用难度高）。
- 修复：认领前前端二次确认（列出待认领任务由用户勾选），或认领时剥离/要求重新输入渠道密钥。

### R6-I5 · admin 手动改档不同步 `quota_reset_at`，与 webhook/退款口径打架
- `backend/app/api/routers/admin.py:172-230` vs `pay.py:105`（`apply_tier_grant` 写 `quota_reset_at = base+30d`）、`admin.py:496-501`（refund 重置 `now+30d`）
- 依据：`PATCH /users/{id} {"tier":"pro"}` 只写 tier/tier_expires_at；管理员补单的会员周期与配额周期错位——R4 在 webhook 路径修过的同一个 bug，admin 路径漏了。
- 修复：`patch_user` 改 tier 时复用 `apply_tier_grant`，或同步写 `quota_reset_at`。

### R6-I6 · engine 进程从不调用 `configure_logging()`，引擎日志进不了 `logs/app.log`
- `backend/app/core/logging.py:12` vs `backend/app/main.py:34`（唯一调用处）
- 依据：独立引擎进程（`python -m app.services.engine`）不走 main.py，structlog 保持默认配置（stdout 非 JSON 无文件）；admin `GET /api/admin/system` 的 `log_tail` 读 `logs/app.log` 永远看不到引擎日志——而排障最需要的恰恰是引擎日志。
- 修复：engine `_amain()` 入口调 `configure_logging()`。

### R6-I7 · analytics 不用 `part_number` 快照列（R5-B-N2 漏了 analytics）
- `backend/app/api/routers/analytics.py:68`：`pn = t.part_number if t else "未知"`
- 依据：history.py 的 events/releases 已改走 `Notification.part_number` 快照，analytics 仍读实时 `t.part_number`，已删任务的事件显示"未知"，两处口径不一致。
- 修复：`pn = n.part_number or (t.part_number if t else "") or "未知"`。

### R6-I8 · "按天"口径三处打架（UTC vs 北京时间+8h）
- `backend/app/api/routers/admin.py:86-93`（overview 用 `func.date(created_at, "+8 hours")`）vs `:119-133`（traffic 用 `func.date()` UTC）、`history.py`（releases UTC）、`analytics.py`（overview `by_day` UTC）
- 依据：北京时间 0–8 点的事件在放货记录/分析里归到前一天；同一后台"今日推送"和"流量看板"的"今天"不是同一天。
- 修复：统一走北京时间口径（`func.date(created_at, '+8 hours')`）。

### R6-I9 · 认领后暂停态不断裂但承诺落空
- `backend/app/api/routers/auth.py:107-136`
- 依据：`_claim_device_tasks` 只过户 `user_id`、不恢复 `paused`；trial 配额耗尽被自动暂停的任务，用户注册认领后仍是 paused；前端横幅写"去注册/升级继续监控"，实际不点"恢复监控"就不跑。
- 修复：认领时对"因配额耗尽自动暂停"的任务自动恢复（按暂停原因字段区分），或在横幅/注册成功页明确提示去点恢复。

### R6-I10 · 同一概念两处文案口径不一
- `frontend/src/pages/History.tsx:180`（活动日志裸显 `{e.channel}` 英文 key）vs `NotifyChannels.tsx`（`CHANNEL_LABEL` 映射中文）
- 修复：复用同一映射。

### R6-I11 · trial 耗尽提示文案指错对象
- `frontend/src/pages/Home.tsx`（`trialExhausted` 横幅）
- 依据：横幅写"体验推送已用完，去注册/升级继续监控"，但被 admin 授予 trial 档的**已注册**用户也会命中，"去注册"对他无意义。
- 修复：按 `me===null` 区分文案（匿名→"去注册"，已登录→"去升级"）。

### R6-I12 · Login 忽略 `totp_required`
- `frontend/src/pages/Login.tsx:53`
- 依据：登录后直接 `window.location.href='/'`，不处理返回体里的 `totp_required`；管理员从前台登录（`ADMIN_TOTP_REQUIRED=1`）会被静默建成普通会话，随后访问后台被 403 踢回，全程无解释。后端 `deps.py:63-75` 对 admin 接口强制校验 `totp_verified`，无越权，纯 UX 断层。另：后端在返回 `totp_required=true` **之前**已 Set-Cookie（`auth.py:244-256`），虽被兜住无越权，建议顺手收紧为 TOTP 通过后再发 cookie。
- 修复：`totp_required===true` 时提示"管理员请前往后台登录完成 TOTP 验证"；后端 cookie 改 TOTP 通过后发放。

### R6-I13 · `/me` 任何失败都被当成匿名
- `frontend/src/components/App.tsx:57-74`
- 依据：catch 里无条件 `setMe(null)`；后端 500/网络抖动时已登录用户看到"匿名体验中"横幅（文案"可创建 1 个任务"与他实际档位不符）。
- 修复：区分 401（真匿名）与其他错误（错误态/重试）。

### R6-I14 · 门店目录刷新 429 会清空已有列表
- `frontend/src/pages/AddMonitor.tsx:66-84`
- 依据：`loadStores(1)` 只对 403 做了 toast 降级；管理员触发刷新冷却（后端 429 `refresh_limited`）走 else 分支 `setStores(null)`，已加载列表被清空变 ErrorState。
- 修复：429 也走 toast，不清空 `stores`。

---

## 三、UI 问题（4 条）

- **R6-U1** 验证码重发成功零反馈 — `frontend/src/pages/VerifyEmail.tsx:78-95`：`resend` 成功后只 `setError(null)`。修复：加一行成功提示。
- **R6-U2** 会员表空状态列数错位 — `admin/src/features/members/index.tsx:281`：`colSpan={7}`，表头 8 列。修复：`colSpan={8}`。
- **R6-U3** 续期失败用原生 `alert` — `frontend/src/pages/Home.tsx:68`，与全站风格割裂。修复：改行内错误文案（参考 TaskDetail 的 `renewMsg` 模式）。
- **R6-U4** 测试接口失败时丢掉后端错误原因 — `frontend/src/components/NotifyChannels.tsx:45-58`：`notify_test` 返回 `{ok, status, error}`（无 `message`），`ok:false` 时前端只显示泛泛"发送失败"。修复：优先展示 `res.error`。

---

## 四、P2（22 条）

**部署/配置**
- P2-1 `env_val` 静默吃掉引号内的空格 — `deploy/deploy.sh:45`、`deploy/backup.sh` 同函数：`tr -d " '\"\t\r"` 删除值里所有空格，`SMTP_FROM="StockMon <noreply@glint.red>"` → `StockMon<noreply@glint.red>`。当前被读取的键都不含合法空格，属 latent。修复：只 strip 首尾空白。
- P2-2 `admin/package.json:22` 仍有 `@clerk/react` 依赖（src 零引用，仅剩两个无人引用的 logo 文件）。修复：`npm uninstall @clerk/react` + 删 logo 文件。
- P2-3 `admin/` 双 lockfile 并存（`package-lock.json` + `pnpm-lock.yaml`，后者已漂移）。修复：删 `pnpm-lock.yaml`（仓库已选 npm 路线）。
- P2-4 `admin/package.json:3` `"private": false` 模板残留，有误 publish 风险。修复：`"private": true`。
- P2-5 `deploy/backup.sh:73-76` 恢复注释没写"先停服务"（WAL 下运行中 restore 有损坏风险）。修复：注释首行加 `systemctl stop`。
- P2-6 `env_val` 取首个匹配 vs `source` 取最后一个（`deploy.sh:33` `head -n1`）。修复：取最后一个或重复键告警。

**后端安全/质量**
- P2-7 `/verify-email` 无尝试限流（`auth.py:175-198`）：6 位码仅靠全局 60/min/IP。修复：按 email 记失败次数，5 次锁定 15 分钟。
- P2-8 `/auth/totp/verify` 无独立限流：6 位 TOTP（`valid_window=1`）可被低速爆破。修复：加尝试限制。
- P2-9 邮件滥用面：`resend-code` 无 IP 维度总量刹车（不同邮箱各 3 次/小时，全局 60/min/IP 下约 3600 封/小时/IP 当中继）；`notify/test` 可向任意地址发信。修复：resend 加 IP 维度限流；test 的 target 必须等于用户本人邮箱（除管理员）。
- P2-10 匿名创建/`409` 查重/`batch` 上限都是 check-then-insert（`tasks.py:126-145,215-252`）：并发可建出重复任务或超上限。修复：客户端幂等键（`Idempotency-Key` + 唯一约束表）。
- P2-11 门店目录刷新占位烧掉 1 小时冷却（`catalog.py:144` vs `179-185`）：`_do_refresh_stores` 被 Apple 限流时直接 return，不重置 `REFRESH_AT_KEY` 占位 → 没刷成却 1 小时拒绝重试。修复：限流 abort 时写回 0，或只在成功时写占位。
- P2-12 `expires_at` 可传过去时间（`tasks.py:57-65`）：登录用户传过去时间建出永不轮询的僵尸任务。修复：`< now` 时 400。
- P2-13 `TaskOut` 等 response_model 返回 naive datetime 无 `Z` 后缀，与手写端口 `.isoformat()+"Z"` 惯例不一致。修复：统一带 `Z` 或 aware datetime。
- P2-14 缺索引：`_retry_pending_notifications` 每 5 秒 `WHERE status='failed' AND retry_at<=now` 全表扫；lifecycle 按天去重查 `(kind, created_at)` 无索引；notifications 只增不减。修复：加 `(status, retry_at)`、`(kind, created_at)` 复合索引 + 保留期清理。
- P2-15 中间件每个 `/api` 请求写一行 `api_hits` 并 commit（`main.py:122-145`），未认证垃圾流量也写，与引擎/sweep 争 SQLite 写锁。修复：采样写入（如 10%）或内存批量刷盘；`prune` 改每天一次。
- P2-16 `sms_to` 死代码（`notifier.py:159-160`）：`ChannelsIn(extra="forbid")` 使 API 产不出 `sms_to`，但 `dispatch` 仍处理 → DB 被直写时 `send_sms` 必 raise 空烧 3 次重试。修复：删分支。
- P2-17 本地 dev DB schema 停在 `0001_initial`（gitignored）：`alembic check` FAILED，新人本地起服务不手动 upgrade 会撞缺列。修复：文档/启动脚本加 `alembic upgrade head` 前置检查。
- P2-18 验证码比较不用恒定时间比较（`auth.py:183`）。修复：`hmac.compare_digest`。
- P2-19 `_mask_target` 未覆盖 `page` 通道（`notify.py:38-44`）：trial 站内触达的 `target=device_id` 在 `/notifications` 明文返回。修复：一并掩码。
- P2-20 history 的 store 过滤未转义 LIKE 通配符（`history.py`）：`%`/`_` 被当通配符，语义不准（参数化无注入风险）。
- P2-21 `_fire` 内先逐条 commit 通知、后扣配额：两步之间崩溃致"已发送但未计数"的配额漏扣（概率极低）。可接受，或同一事务。

## 修复优先级建议

1. R6-D3（管理员 5 分钟掉档，部署后必踩）→ 2. R6-D1（退避架空，影响 Apple 接口存活）→ 3. R6-D2（资损：60天变30天）→ 4. R6-D4（静默丢通知）→ 5. R6-D5/D6/I3（一对：错误态+错误边界+脏数据防御）→ 6. R6-D7/D8（部署）→ 7. R6-I4（越权原语）→ 8. 其余不自洽/UI → 9. P2 批量

## 已知遗留（不计入，与前轮一致）

爱发电签名真实对拍（等用户侧就绪，唯一外部 blocker）、对账定时 job TODO、对标"可以晚点"项（微信通知、3 秒刷新档、全站城市榜单、后台运营四件套、冷启动种子数据）。
