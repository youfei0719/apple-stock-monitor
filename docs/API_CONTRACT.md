# API 契约 v1（前后端/后台共同遵守）

Base URL: `https://stock.glint.red/api`（开发环境 `http://localhost:8000/api`）
认证：HttpOnly + Secure + SameSite=Lax Cookie（session token），JSON 接口。
所有时间：UTC ISO8601。错误格式：`{"detail": "...", "code": "..."}`。全局 60 次/分钟/IP 限流的 429 响应带 `Retry-After` 秒数；专项限流以错误文案为准。

## 健康检查
- `GET /healthz` → 200 `{"status":"ok","db":true,"engine":"running","engine_in_api":false,"version":"..."}`（无需认证）；**DB 挂时返回 503** `{"status":"degraded","db":false,"engine":"<state>","engine_in_api":false,"version":"..."}`（deploy 健康检查据此判失败）

## 认证 / 用户
- `POST /api/auth/register` `{email, password}` → 201 `{id, email, tier:"free"}`；注册后发 6 位邮箱验证码（10 分钟有效），同 `X-Device-Id` 的匿名任务自动迁移绑定到新用户（迁移后原行 `device_id` 清空）。prod 启动硬门槛：`SMTP_HOST/SMTP_USER/SMTP_PASSWORD` 缺失则拒绝启动（防止验证码发不出导致用户永远 403 登录的死胡同）
- `POST /api/auth/verify-email` `{email, code}` → `{ok:true}`（验证码通过后 `email_verified=true`）
- `POST /api/auth/login` `{email, password}` → 200 `{ok:true, totp_required:false}` + Set-Cookie（session_token），失败 401；连续 5 次失败锁 IP 15 分钟；**邮箱未验证 → 403 `{code:"email_unverified"}`**（前端据此提示去验证）；登录成功同样迁移同 `X-Device-Id` 匿名任务
- `POST /api/auth/logout` → 204
- `GET /api/me` → `{id, email, tier（有效档位）, quota:{push_used, push_limit, tasks_used, tasks_limit}, totp_enabled}`
- `PATCH /api/auth/me` `{old_password, password}` → 200 `{ok:true}`（改密；注意后端没有 PATCH /api/me，me_router 只有 GET 别名，调错路径 405）
- `POST /api/auth/password-reset/request` `{email}` → `{ok:true, message}`；仅给已验证账户邮箱发 6 位重置码，未知邮箱／未验证邮箱同样返回通用提示。与注册验证码隔离；每邮箱 3 次/小时，每 IP 20 次/小时；10 分钟有效，重发替换旧码，数据库只存服务端密钥 HMAC。
- `POST /api/auth/password-reset/confirm` `{email, code, password}` → `{ok:true}`；密码至少 8 位且含字母、数字；错误码持久化尝试锁定，验证码原子消费，一次有效；成功注销全部会话，保留 TOTP 配置，随后重新登录。不会创建登录会话。
- `POST /api/auth/totp/setup` / `POST /api/auth/totp/verify`（管理员强制，普通用户可选）

## 监控任务
Task: `{id, config_revision, name, group, category, part_number, product_name, color, capacity, stores:[{number,name,city}], mode:"instant"|"confirmed", repeat_interval_sec|null, channels:{bark_key?, webhooks?[], email?}, paused, paused_reason|null, expires_at, created_at, last_polled_at|null, last_poll_ok|null, refresh_interval_sec, creation_status:null|"created"|"existing", latest}`

其中 `latest` 为后端聚合的最新状态摘要（snake_case）：
`{stores:{<store_number>:{<part_number>:{state, pickup_display, store_pick_eligible, pickup_search_quote, updated_at}}}, available_count, total}`
- `last_polled_at` 为最近实际查询尝试，`last_poll_ok` 为该次是否成功，`refresh_interval_sec` 按有效档位配置返回目标周期，不承诺查询一定准时。库存记录 `updated_at` 在相同结果及失败检查时也推进；前端依据各门店记录年龄判断是否陈旧。
- `config_revision` 是用户可编辑设置的 SHA256 摘要，库存轮询和检查时间变化不改变它。前端编辑草稿保留读取时的摘要；刷新库存不会更新草稿摘要。
- 创建响应的 `creation_status` 区分新建与幂等返回的现有任务；现有任务保留原设置，不会因重复提交覆盖配置。普通查询返回 null。
- state ∈ 展示七态（后端已按 paused/verifying/cooling/expired 换算），不是 Apple 原始字段。

- `GET /api/tasks` → 列表（含每任务最新状态摘要）
- `POST /api/tasks` → 201（按 tier 校验任务数上限）。支持 `Idempotency-Key` 请求头：重复 key 直接返回首次创建的任务（网络重试/重复提交不建重复任务）；同 key 首个请求仍在创建中时返回 409 `idempotency_in_progress`。R24 起同内容重复提交（不同 key，如双 Tab/网络重试）不再 409 `task_conflict`，而是幂等返回已建任务（服务端按归属+型号+门店组合派生内容键串行化并发创建；并发创建中返回 409 `idempotency_in_progress`）。
- `POST /api/tasks/batch` `{part_numbers[], store_numbers[], name_template, category?, group?, mode?, repeat_interval_sec?, channels?}` → 门店×型号批量生成。同样支持 `Idempotency-Key`（语义同上：重复 key 返回首次创建结果，创建中返回 409 `idempotency_in_progress`）；同内容重复批量提交同样幂等返回首次创建的任务（R24）。
- 单建和批量创建的 `repeat_interval_sec` 最小 60 秒整数。显式 null 表示关闭；省略字段时兼容历史 Pro 默认 300 秒，其余默认关闭。新前端始终明确提交开关值，重复提醒与首次确认模式独立。
- `PATCH /api/tasks/{id}` 可带 `config_revision`；服务端加写锁后读取最新设置，摘要不一致返回 409 `config_conflict`，不覆盖新设置；省略字段兼容旧客户端。
- `PATCH /api/tasks/{id}`（改名/分组/暂停/有效期/渠道/门店/首次确认模式/重复间隔）。`stores` 为完整替换列表，1–20 家，不可重复或为空；归一化门店编号，冲突拒绝；移除门店的旧状态、通知基线和过期幂等映射随本次事务清理。省略重复间隔保留原值，显式 null 关闭。
- `POST /api/tasks/group-action` `{group, paused}` → `Task[]`；仅登录用户本人目标下的所有任务，单次事务一起暂停／恢复，包含过期任务；恢复不延长有效期，暂停仍占名额。
- `GET /api/tasks/group-settings?group=` → 当前用户该购买目标的全部 Task，包含已暂停和已过期任务；不存在返回 404 `group_not_found`。
- `PATCH /api/tasks/group-settings` `{group, expected:[{id,config_revision}], patch:{group?,mode?,repeat_interval_sec?,email?}}` → 更新后的 Task 数组。受影响成员必须与当前账号该组的成员完全一致，设置摘要也必须一致，否则 409 `config_conflict`；写锁后校验并一次提交。只修改勾选字段；repeat 显式 null 关闭，email 显式 null 使用已验证注册邮箱并保留其他渠道。重复间隔 ≥60；group 去空白后不能为空，不合并现有同名组（409 `group_exists`）；空 patch 返回 400。机型、门店、暂停、有效期保持原值。此接口只允许登录账号编辑本人任务，不能按匿名设备或外部任务 ID 批量更新。
- `DELETE /api/tasks/{id}`

## 目录（公开）
- `GET /api/catalog/stores?refresh=0` → `[{number, name, city, province}]`（全国直营店；**refresh=1 已 403** `{code:"use_admin_refresh"}`，在线刷新能力已移至管理后台 `POST /api/admin/catalog/refresh`（TOTP 二次验证，全局 1 次/小时））
- `GET /api/catalog/products?category=iphone|ipad|mac|watch` → `[{part_number, name, color, capacity, price_cny}]`
- `GET /api/catalog/anchors` → `[城市名, ...]`（城市锚点列表，调试/管理用；空库时自动播种种子锚点）

## 库存状态（七态，前端展示用，全部 snake_case）
state ∈ `available | unavailable | unknown | verifying | cooling | paused | expired`
- `expired` 为任务级过期（`task.expires_at` 已过，与门店×型号维度的六态不同）：整任务过期后展示态直接为 `expired`，不再看各组合状态；`GET /api/tasks?status=expired` 可单独拉过期任务。R11-P1-6：契约此前写"六态"已修正为七态。
- `GET /api/tasks/{id}/states` → `[{store_number, part_number, state, pickup_display, store_pick_eligible, pickup_search_quote, confirmed_count, last_event_at, updated_at}]`

## 通知
- `POST /api/notify/test` `{channel, target}` → 发送测试通知，返回成功/失败（链路测试）
- `GET /api/notifications?task_id=` → 通知历史
- `GET /api/notify/channels/health`（需登录）→ `{channels: [{key, name, configured, configured_basis, success_rate_7d, last_failure_at, last_failure_reason}]}`（各通道近 7 天成功率 + 最后失败原因；五通道：bark/wecom（企微）/dingtalk（钉钉）/feishu（飞书）/email；`success_rate_7d` 只统计 sent/failed，skipped 不计入，无数据时为 `null`；`configured` 为近似口径：用户任务里配过该通道，或近 30 天有该通道的通知记录）
- 通知直达链接由后端生成：`https://www.apple.com.cn/shop/buy-iphone/...` 或购物袋 URL
- 系统通知 kind（不扣配额）：`quota_warning`（配额 80%/100% 预警）· `quota_exhausted`（配额耗尽自动暂停）· `task_auto_paused`（连续 10 次发送失败自动暂停，走邮件兜底）；配额口径为**实际发送成功的通知条数**，失败回滚不扣、重试成功不重复扣

## 历史与数据（全部 snake_case）
- `GET /api/history/events?part_number=&store=&days=30` → 有货事件（活动日志）：`[{id, task_id, part_number, title, body, link, channel, created_at}]`
- `GET /api/history/releases?days=7` → 放货记录（按天×机型聚合）：`[{day, part_number, events}]`（standard 及以上可用）
- `GET /api/analytics/ranking?days=1` → 个人城市放货排行：`{scope: "personal", ranking: [{city, events}]}`（按当前用户自己的有货通知聚合，非全站榜单；days=1 是滚动近 24 小时，不是北京时间今日零点起）
- `GET /api/analytics/overview` → 数据分析摘要：`{days, total_events, by_part:[[part_number,count]...], by_day:[[day,count]...]}`
- `GET /api/guide/purchase` → 到货购买指南（静态内容）：`{title, steps[]}`
- `GET /api/stats/poll` 的 `polled_tasks` 表示有过检查尝试的任务数（含旧记录／失败），不是本轮完成数。历史页另用 `GET /api/tasks` 展示全部现存任务的创建时间、最近尝试、已存结果及当前有效门店覆盖；没有逐轮检查日志，不声称能还原完整历史覆盖率。
- `GET /api/stats/poll` → 上次查询/成功率/平均响应（按用户任务聚合）：`{tasks, polled_tasks, success_rate, avg_response_ms, last_poll_at, engine}`

## 配额与会员
- `GET /api/quota` → `{tier（有效档位，过期按 free）, tier_expires_at, pending_tier, quota_reset_at（配额周期锚点 ISO，前端按北京时间展示）, push_used, push_limit, tasks_used, tasks_limit, refresh_interval_sec, period（锚点日期 YYYY-MM-DD）}`；配额周期为购买日+30天滚动
  - `pending_tier`：降级预约档位。用户从 standard/pro 降级时不立即切换、只写 `pending_tier`，到期 sweep 才切换为它；无预约时为 `null`。前端可在到期前展示"到期后切换为 X"。
  - 匿名 trial 分支（未登录 + 携带 `X-Device-Id`）：返回 `{tier:"trial", tier_expires_at:null, pending_tier:null, quota_reset_at:null, period:"YYYY-MM"}`。
    配额为**自然月口径**：用量键 `trial_quota:{device_id}:{YYYY-MM}`（月份取北京时间（UTC+8）`now`），每月 1 日（北京时间）自动归零；与登录用户的"购买日+30天滚动"区分。
- `GET /api/plans` → 三档说明（公开，**不再返回 trial**）：`[{tier, name, price_cny, tasks_limit, push_limit, channels[], history, priority, refresh_interval_sec}]`
  - free：免费 · standard：标准（¥9.9/月） · pro：Pro（¥19.9/月）；trial 只用于未登录匿名体验，不可购买
  - 前端渲染注意：无 `features`/`period`/`id` 字段；档位名用 `name`，价格用 `price_cny`，周期文案前端自拼（`price_cny>0` → "¥X / 月"）；"当前"徽章用 `p.tier === me.tier` 判断。
- `GET /api/site-config`（公开）→ `{afdian_page_url}`：爱发电自家赞助页 URL（前端付费指引跳转用，`AFDIAN_PAGE_URL` 环境变量配置）

### "完整历史"档位语义（2026-10-09 定稿，精确到接口）

`tiers.py` 中 `history: True/False` 的门控**只作用于一个接口**，其余数据接口对所有登录用户开放：

| 接口 | 档位门控 | 说明 |
|---|---|---|
| `GET /api/history/releases`（放货记录：按天×机型聚合） | ✅ standard/pro 才可见；trial/free → 403 `{code:"tier_required"}` | 这就是"完整历史"所指的全部含义 |
| `GET /api/history/events?days=`（有货事件活动日志，days 1–365，默认 30） | ❌ 无门控（含 free） | 只返回自己任务成功发送过的到货通知 |
| `GET /api/analytics/ranking`（城市放货排行） | ❌ 无门控 | 按当前用户自己的有货通知聚合，非全站榜单 |
| `GET /api/analytics/overview` | ❌ 无门控 | 同上，本用户数据摘要 |
| `GET /api/stats/poll` | ❌ 无门控 | 按用户任务聚合的查询统计 |

- 降级后历史数据仅被 403 隐藏、不删除（断裂-1 修完后到期降级路径可达）。
- 注意实现细节：`history.py` 门控用的是 `effective_tier_of(user)["history"]`（档位过期按 free 算；见断裂-1，已修完），此处语义为"完整历史"= `/history/releases` 可见性。

## 支付（爱发电）
- `POST /api/pay/afdian-webhook`（签名校验）→ 自动开通/续期会员，写 payments 表；升级/续费立即生效（配额锚点同步+30天），**降级到期生效**（只写 `pending_tier`，到期 sweep 切换）；金额与档位不符时仍落库 `status='amount_mismatch'` 待人工处理（返回 200 + `pending_count`）
- `GET /api/payments` → 当前用户付费记录

## 后台（/api/admin/*，需 admin 会话 + TOTP；守卫要求 session totp_verified，否则 403 {code:"totp_required"}）
- `GET /api/admin/overview` → `{total_users, tier_distribution: {tier: count}（DB 原始值，向后兼容；trial 为匿名体验不落库，用户表只有 free/standard/pro）, effective_tier_distribution: {tier: count}（按有效档位口径：已到期未降档的付费用户计入 free）, paid_members: int（有效付费会员数 = standard + pro 有效档位）, today_pushes, revenue_cny: float, active_tasks, pending_payments: {unclaimed, amount_mismatch}}`
- `GET /api/admin/traffic?days=30` → 数组元素 `{day: "2026-10-09", pv, uv}`
- `GET /api/admin/users?q=&tier=` → 数组元素 `{id: int, email, tier（含 trial）, tier_expires_at, email_verified, is_admin, totp_enabled, created_at}`；`PATCH /api/admin/users/{id}` 接受 `{tier, tier_expires_at?, is_admin?, paused_tasks?, email_verified?}` → `{ok, changes}`（补单时可填到期时间；退款反向操作：`{"tier":"free"}` 手动降级；SMTP 故障时可传 `{"email_verified":true}` 手动验邮，记审计）
- `GET /api/admin/payments?status=&claim_status=unclaimed` → 数组元素 `{id: int, user_id, order_id, plan（爱发电 plan_id 字符串）, amount_cny, tier_from, tier_to, status（paid/amount_mismatch/unknown_plan/refunded/resolved/cancelled；unknown_plan=爱发电 plan_id 未知，待人工）, remark（从 raw_payload 提取）, created_at}`；`claim_status=unclaimed` 筛出待认领订单；无 email 字段
- `POST /api/admin/payments/{id}/claim` `{"user_id": int}` → 认领订单：绑定用户 + 按订单档位开通 30 天（到期/锚点同步）+ 回填 `Payment.user_id`；若为 `amount_mismatch` 成功后置 `status='resolved'` 并待处理计数 -1 → `{ok, payment_id, user_id, tier, tier_expires_at, payment_status, pending_count?}`（记审计）
- `POST /api/admin/payments/{id}/close` → 不予开通直接关闭：`status='resolved'`（出待处理队列，`amount_mismatch` 时待处理计数 -1），不绑定用户、不开通档位（记审计）
- `POST /api/admin/payments/{id}/refund` → 标记退款：`status='refunded'`（revenue 只计 paid，自动排除）+ 该用户降回 `free` 并清空 `tier_expires_at`/`pending_tier`（记审计）
- `GET /api/admin/system` → `{engine: {running, last_heartbeat, last_tick_at, rounds_total, rounds_ok, last_error}, apple_cooldown: dict, peak_mode: bool, log_tail: string[]}`；engine 状态读独立引擎进程每 tick 写进 DB 的心跳（60s 内 = running），API 进程自身不跑引擎（`ENGINE_ENABLED=false`）
- `POST /api/admin/system/peak-mode` `{"enabled": bool}` → 高峰模式开关（开启后 trial/free 刷新间隔 ×4；记审计）
- `POST /api/admin/catalog/refresh` → 门店目录在线刷新（重操作：在线打 Apple 接口；管理后台系统页"刷新门店目录"卡片调用）：成功立即返回当前目录数组 `[{number, name, city, province}]`（刷新走后台异步任务，前端稍后重拉即可）；全局限流 1 次/小时，超限 429 `{code:"refresh_limited"}`；TOTP 未验证时 403 `{code:"totp_required"}`
- `GET /api/admin/audit` → 数组元素 `{id, admin_id, action, target_type, target_id, detail, ip, created_at}`
- `POST /api/auth/password-reset/request` `{email}` → `{ok:true, message}`；仅给已验证账户邮箱发 6 位重置码，未知邮箱／未验证邮箱同样返回通用提示。与注册验证码隔离；每邮箱 3 次/小时，每 IP 20 次/小时；10 分钟有效，重发替换旧码，数据库只存服务端密钥 HMAC。
- `POST /api/auth/password-reset/confirm` `{email, code, password}` → `{ok:true}`；密码至少 8 位且含字母、数字；错误码持久化尝试锁定，验证码原子消费，一次有效；成功注销全部会话，保留 TOTP 配置，随后重新登录。不会创建登录会话。
- `POST /api/auth/totp/setup` → `{secret, uri, enabled}`；`POST /api/auth/totp/verify {code}` → `{ok, totp_enabled}`（verify 后当前 session 标 totp_verified）
- 所有写操作记 audit log。

## 限流
- 公开接口：60 req/min/IP；登录接口：5 次失败锁 IP 15 分钟；目录刷新：1 次/小时。

## 补充接口（round4 补齐：认证/任务详情/续期）

- `POST /api/auth/resend-code` `{email}` → 返回 200 `{ok:true}`（防用户枚举：无论邮箱是否注册都不透露；SMTP 发送失败时返回 500 `email_failed`，见下）。
  - 限流：同一邮箱每小时最多 3 次 → 429 `{code:"rate_limited"}`。
  - 邮箱未注册 → 200 `{ok:true}`（不发信，不透露是否注册）；已验证/未验证 → 200 `{ok:true}`（R19 起统一走发码流程，不再带 `already` 标记，三种邮箱响应不可区分，防用户枚举）；邮件发送失败 → 500 `{code:"email_failed"}`。
  - 典型用途：注册后没收到验证码、验证码过期（过期文案见 R4-P1-D6，引导用户点"重新发送"而非重新注册）。

- `GET /api/tasks/{id}` → 200 Task（与 `POST /api/tasks` 返回同形，含 `channels`、`latest` 状态摘要，见"监控任务"节）。
  - 鉴权：登录用户只能看自己的；未登录时按 `X-Device-Id` 匹配自己的匿名任务，否则 403 `{code:"forbidden"}`；任务不存在 404 `{code:"not_found"}`。

- `POST /api/tasks/{id}/renew` → 200 Task（同形）。
  - 一键续期：`expires_at = max(now, 原 expires_at) + 30 天`。只延长时间，不改 `paused`/配额状态。
  - 匿名 trial 任务受 24h 上限钳制（`expires_at` 会被 clamp 到 now+24h）。
  - 注意：提前续期**保留**剩余天数（实现语义 `max(now, expires_at)+30天`；R4-P2：此前一律 now+30d，剩 20 天时续期反而亏，已修正）。

- 到货提醒：`instant` 首次查到有货或无货转有货时发送；`confirmed` 需连续两次有货。持续有货仅在开启重复提醒时再次发送，失败查询不计入连续确认。
