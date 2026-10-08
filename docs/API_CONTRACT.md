# API 契约 v1（前后端/后台共同遵守）

Base URL: `https://stock.glint.red/api`（开发环境 `http://localhost:8000/api`）
认证：HttpOnly + Secure + SameSite=Lax Cookie（session token），JSON 接口。
所有时间：UTC ISO8601。错误格式：`{"detail": "...", "code": "..."}`。

## 健康检查
- `GET /healthz` → `{"status":"ok","db":true,"engine":"running","version":"..."}`（无需认证）

## 认证 / 用户
- `POST /api/auth/register` `{email, password}` → 201 `{id, email, tier:"free"}`；注册后发 6 位邮箱验证码（10 分钟有效），同 `X-Device-Id` 的匿名任务自动迁移绑定到新用户（迁移后原行 `device_id` 清空）。prod 启动硬门槛：`SMTP_HOST/SMTP_USER/SMTP_PASSWORD` 缺失则拒绝启动（防止验证码发不出导致用户永远 403 登录的死胡同）
- `POST /api/auth/verify-email` `{email, code}` → `{ok:true}`（验证码通过后 `email_verified=true`）
- `POST /api/auth/login` `{email, password}` → 200 `{ok:true, totp_required:false}` + Set-Cookie（session_token），失败 401；连续 5 次失败锁 IP 15 分钟；**邮箱未验证 → 403 `{code:"email_unverified"}`**（前端据此提示去验证）；登录成功同样迁移同 `X-Device-Id` 匿名任务
- `POST /api/auth/logout` → 204
- `GET /api/me` → `{id, email, tier（有效档位）, quota:{push_used, push_limit, tasks_used, tasks_limit}, totp_enabled}`
- `PATCH /api/auth/me` `{password}` → 200 `{ok:true}`（改密）
- `POST /api/auth/totp/setup` / `POST /api/auth/totp/verify`（管理员强制，普通用户可选）

## 监控任务
Task: `{id, name, group, category, part_number, product_name, color, capacity, stores:[{number,name,city}], mode:"instant"|"confirmed", repeat_interval_sec|null, channels:{bark_key?, webhooks?[], email?}, paused, expires_at, created_at, latest}`

其中 `latest` 为后端聚合的最新状态摘要（snake_case）：
`{stores:{<store_number>:{<part_number>:{state, pickup_display, store_pick_eligible, pickup_search_quote, updated_at}}}, available_count, total}`
- state ∈ 展示六态（后端已按 paused/verifying/cooling 换算），不是 Apple 原始字段。

- `GET /api/tasks` → 列表（含每任务最新状态摘要）
- `POST /api/tasks` → 201（按 tier 校验任务数上限）
- `POST /api/tasks/batch` `{part_numbers[], store_numbers[], name_template}` → 门店×型号批量生成
- `PATCH /api/tasks/{id}`（改名/分组/暂停/有效期/渠道）
- `DELETE /api/tasks/{id}`

## 目录（公开）
- `GET /api/catalog/stores?refresh=0` → `[{number, name, city, province}]`（全国直营店，refresh=1 触发在线刷新）
- `GET /api/catalog/products?category=iphone|ipad|mac|watch` → `[{part_number, name, color, capacity, price_cny}]`

## 库存状态（六态，前端展示用，全部 snake_case）
state ∈ `available | unavailable | unknown | verifying | cooling | paused`
- `GET /api/tasks/{id}/states` → `[{store_number, part_number, state, pickup_display, store_pick_eligible, pickup_search_quote, confirmed_count, last_event_at, updated_at}]`

## 通知
- `POST /api/notify/test` `{channel, target}` → 发送测试通知，返回成功/失败（链路测试）
- `GET /api/notifications?task_id=` → 通知历史
- 通知直达链接由后端生成：`https://www.apple.com.cn/shop/buy-iphone/...` 或购物袋 URL
- 系统通知 kind（不扣配额）：`quota_warning`（配额 80%/100% 预警）· `quota_exhausted`（配额耗尽自动暂停）· `task_auto_paused`（连续 10 次发送失败自动暂停，走邮件兜底）；配额口径为**实际发送成功的通知条数**，失败回滚不扣、重试成功不重复扣

## 历史与数据（全部 snake_case）
- `GET /api/history/events?part_number=&store=&days=30` → 有货事件（活动日志）：`[{id, task_id, part_number, title, body, link, channel, created_at}]`
- `GET /api/history/releases?days=7` → 放货记录（按天×机型聚合）：`[{day, part_number, events}]`（standard 及以上可用）
- `GET /api/analytics/ranking?days=1` → 全国榜单（城市放货排行）：`[{city, events}]`
- `GET /api/analytics/overview` → 数据分析摘要：`{days, total_events, by_part:[[part_number,count]...], by_day:[[day,count]...]}`
- `GET /api/guide/purchase` → 到货购买指南（静态内容）：`{title, steps[]}`
- `GET /api/stats/poll` → 上次查询/成功率/平均响应（按用户任务聚合）：`{tasks, polled_tasks, success_rate, avg_response_ms, last_poll_at, engine}`

## 配额与会员
- `GET /api/quota` → `{tier（有效档位，过期按 free）, tier_expires_at, quota_reset_at（配额周期锚点 ISO，前端按北京时间展示）, push_used, push_limit, tasks_used, tasks_limit, refresh_interval_sec, period（锚点日期 YYYY-MM-DD）}`；配额周期为购买日+30天滚动
- `GET /api/plans` → 三档说明（公开，**不再返回 trial**）：`[{tier, name, price_cny, tasks_limit, push_limit, channels[], history, priority, refresh_interval_sec}]`
  - free：免费 · standard：标准（¥19/月） · pro：Pro（¥39/月）；trial 只用于未登录匿名体验，不可购买
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
- 注意实现细节：`history.py` 当前门控用的是 `tier_of(user.tier)`（见断裂-1，有效档位应走 `effective_tier()`），后端 worker 修完后此处语义不变（"完整历史"= `/history/releases` 可见性）。

## 支付（爱发电）
- `POST /api/pay/afdian-webhook`（签名校验）→ 自动开通/续期会员，写 payments 表；升级/续费立即生效（配额锚点同步+30天），**降级到期生效**（只写 `pending_tier`，到期 sweep 切换）；金额与档位不符时仍落库 `status='amount_mismatch'` 待人工处理（返回 200 + `pending_count`）
- `GET /api/payments` → 当前用户付费记录

## 后台（/api/admin/*，需 admin 会话 + TOTP；守卫要求 session totp_verified，否则 403 {code:"totp_required"}）
- `GET /api/admin/overview` → `{total_users, tier_distribution: {tier: count}（含 trial）, today_pushes, revenue_cny: float, active_tasks, pending_payments: {unclaimed, amount_mismatch}}`
- `GET /api/admin/traffic?days=30` → 数组元素 `{day: "2026-10-09", pv, uv}`
- `GET /api/admin/users?q=&tier=` → 数组元素 `{id: int, email, tier（含 trial）, tier_expires_at, is_admin, totp_enabled, created_at}`；`PATCH /api/admin/users/{id}` 接受 `{tier, tier_expires_at?, is_admin?, paused_tasks?, email_verified?}` → `{ok, changes}`（补单时可填到期时间；退款反向操作：`{"tier":"free"}` 手动降级；SMTP 故障时可传 `{"email_verified":true}` 手动验邮，记审计）
- `GET /api/admin/payments?status=&claim_status=unclaimed` → 数组元素 `{id: int, user_id, order_id, plan（爱发电 plan_id 字符串）, amount_cny, tier_from, tier_to, status（paid/refunded/cancelled/amount_mismatch）, remark（从 raw_payload 提取）, created_at}`；`claim_status=unclaimed` 筛出待认领订单；无 email 字段
- `POST /api/admin/payments/{id}/claim` `{"user_id": int}` → 认领订单：绑定用户 + 按订单档位开通 30 天（到期/锚点同步）+ 回填 `Payment.user_id`；若为 `amount_mismatch` 成功后置 `status='resolved'` 并待处理计数 -1 → `{ok, payment_id, user_id, tier, tier_expires_at, payment_status, pending_count?}`（记审计）
- `POST /api/admin/payments/{id}/close` → 不予开通直接关闭：`status='resolved'`（出待处理队列，`amount_mismatch` 时待处理计数 -1），不绑定用户、不开通档位（记审计）
- `POST /api/admin/payments/{id}/refund` → 标记退款：`status='refunded'`（revenue 只计 paid，自动排除）+ 该用户降回 `free` 并清空 `tier_expires_at`/`pending_tier`（记审计）
- `GET /api/admin/system` → `{engine: {running, last_heartbeat, last_tick_at, rounds_total, rounds_ok, last_error}, apple_cooldown: dict, peak_mode: bool, log_tail: string[]}`；engine 状态读独立引擎进程每 tick 写进 DB 的心跳（60s 内 = running），API 进程自身不跑引擎（`ENGINE_ENABLED=false`）
- `POST /api/admin/system/peak-mode` `{"enabled": bool}` → 高峰模式开关（开启后 trial/free 刷新间隔 ×4；记审计）
- `GET /api/admin/audit` → 数组元素 `{id, admin_id, action, target_type, target_id, detail, ip, created_at}`
- `POST /api/auth/totp/setup` → `{secret, uri, enabled}`；`POST /api/auth/totp/verify {code}` → `{ok, totp_enabled}`（verify 后当前 session 标 totp_verified）
- 所有写操作记 audit log。

## 限流
- 公开接口：60 req/min/IP；登录接口：5 次失败锁 IP 15 分钟；目录刷新：1 次/小时。

## 补充接口（round4 补齐：认证/任务详情/续期）

- `POST /api/auth/resend-code` `{email}` → 200 `{ok:true}`；重发 6 位邮箱验证码（10 分钟有效，邮箱大小写归一化）。
  - 限流：同一邮箱每小时最多 3 次 → 429 `{code:"rate_limited"}`。
  - 邮箱未注册 → 404 `{code:"not_found"}`；已验证过 → `{ok:true, already:true}`（不再发信）；邮件发送失败 → 500 `{code:"email_failed"}`。
  - 典型用途：注册后没收到验证码、验证码过期（过期文案见 R4-P1-D6，引导用户点"重新发送"而非重新注册）。

- `GET /api/tasks/{id}` → 200 Task（与 `POST /api/tasks` 返回同形，含 `channels`、`latest` 状态摘要，见"监控任务"节）。
  - 鉴权：登录用户只能看自己的；未登录时按 `X-Device-Id` 匹配自己的匿名任务，否则 403 `{code:"forbidden"}`；任务不存在 404 `{code:"not_found"}`。

- `POST /api/tasks/{id}/renew` → 200 Task（同形）。
  - 一键续期：`expires_at = now + 30 天`。只延长时间，不改 `paused`/配额状态。
  - 匿名 trial 任务受 24h 上限钳制（`expires_at` 会被 clamp 到 now+24h）。
  - 注意：提前续期时**不保留**剩余天数（renew 语义 = 从现在起 30 天）。
