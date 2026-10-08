# API 契约 v1（前后端/后台共同遵守）

Base URL: `https://stock.glint.red/api`（开发环境 `http://localhost:8000/api`）
认证：HttpOnly + Secure + SameSite=Lax Cookie（session token），JSON 接口。
所有时间：UTC ISO8601。错误格式：`{"detail": "...", "code": "..."}`。

## 健康检查
- `GET /healthz` → `{"status":"ok","db":true,"engine":"running","version":"..."}`（无需认证）

## 认证 / 用户
- `POST /api/auth/register` `{email, password}` → 201 `{id, email, tier:"free"}`
- `POST /api/auth/login` `{email, password}` → 200 `{ok:true, totp_required:false}` + Set-Cookie（session_token），失败 401；连续 5 次失败锁 IP 15 分钟
- `POST /api/auth/logout` → 204
- `GET /api/me` → `{id, email, tier, quota:{push_used, push_limit, tasks_used, tasks_limit}, totp_enabled}`
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

## 历史与数据（全部 snake_case）
- `GET /api/history/events?part_number=&store=&days=30` → 有货事件（活动日志）：`[{id, task_id, part_number, title, body, link, channel, created_at}]`
- `GET /api/history/releases?days=7` → 放货记录（按天×机型聚合）：`[{day, part_number, events}]`（standard 及以上可用）
- `GET /api/analytics/ranking?days=1` → 全国榜单（城市放货排行）：`[{city, events}]`
- `GET /api/analytics/overview` → 数据分析摘要：`{days, total_events, by_part:[[part_number,count]...], by_day:[[day,count]...]}`
- `GET /api/guide/purchase` → 到货购买指南（静态内容）：`{title, steps[]}`
- `GET /api/stats/poll` → 上次查询/成功率/平均响应（按用户任务聚合）：`{tasks, polled_tasks, success_rate, avg_response_ms, last_poll_at, engine}`

## 配额与会员
- `GET /api/quota` → `{tier, tier_expires_at, push_used, push_limit, tasks_used, tasks_limit, refresh_interval_sec, period}`
- `GET /api/plans` → 四档说明（公开）：`[{tier, name, price_cny, tasks_limit, push_limit, channels[], history, priority, refresh_interval_sec}]`
  - trial：体验（免费） · free：免费 · standard：标准（¥19/月） · pro：Pro（¥39/月）
  - 前端渲染注意：无 `features`/`period`/`id` 字段；档位名用 `name`，价格用 `price_cny`，周期文案前端自拼（`price_cny>0` → "¥X / 月"）；"当前"徽章用 `p.tier === me.tier` 判断。

## 支付（爱发电）
- `POST /api/pay/afdian-webhook`（签名校验）→ 自动开通/续期会员，写 payments 表
- `GET /api/payments` → 当前用户付费记录

## 后台（/api/admin/*，需 admin 会话 + TOTP；守卫要求 session totp_verified，否则 403 {code:"totp_required"}）
- `GET /api/admin/overview` → `{total_users, tier_distribution: {tier: count}（含 trial）, today_pushes, revenue_cny: float, active_tasks}`
- `GET /api/admin/traffic?days=30` → 数组元素 `{day: "2026-10-09", pv, uv}`
- `GET /api/admin/users?q=&tier=` → 数组元素 `{id: int, email, tier（含 trial）, tier_expires_at, is_admin, totp_enabled, created_at}`；`PATCH /api/admin/users/{id}` 接受 `{tier, is_admin?, paused_tasks?}` → `{ok, changes}`
- `GET /api/admin/payments` → 数组元素 `{id: int, user_id, order_id, plan（爱发电 plan_id 字符串）, amount_cny, tier_from, tier_to, status（仅 'paid'）, created_at}`；无 email 字段
- `GET /api/admin/system` → `{engine: {running, last_heartbeat, last_tick_at, rounds_total, rounds_ok, last_error}, apple_cooldown: dict, log_tail: string[]}`
- `GET /api/admin/audit` → 数组元素 `{id, admin_id, action, target_type, target_id, detail, ip, created_at}`
- `POST /api/auth/totp/setup` → `{secret, uri, enabled}`；`POST /api/auth/totp/verify {code}` → `{ok, totp_enabled}`（verify 后当前 session 标 totp_verified）
- 所有写操作记 audit log。

## 限流
- 公开接口：60 req/min/IP；登录接口：5 次失败锁 IP 15 分钟；目录刷新：1 次/小时。
