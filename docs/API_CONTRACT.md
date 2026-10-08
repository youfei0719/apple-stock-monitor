# API 契约 v1（前后端/后台共同遵守）

Base URL: `https://stock.glint.red/api`（开发环境 `http://localhost:8000/api`）
认证：HttpOnly + Secure + SameSite=Lax Cookie（session token），JSON 接口。
所有时间：UTC ISO8601。错误格式：`{"detail": "...", "code": "..."}`。

## 健康检查
- `GET /healthz` → `{"status":"ok","db":true,"engine":"running","version":"..."}`（无需认证）

## 认证 / 用户
- `POST /api/auth/register` `{email, password}` → 201 `{id, email, tier:"free"}`
- `POST /api/auth/login` `{email, password}` → 200（Set-Cookie），失败 401；连续 5 次失败锁 IP 15 分钟
- `POST /api/auth/logout` → 204
- `GET /api/me` → `{id, email, tier, quota:{push_used, push_limit, tasks_used, tasks_limit}, totp_enabled}`
- `PATCH /api/me` `{password?, ...}` → 200
- `POST /api/auth/totp/setup` / `POST /api/auth/totp/verify`（管理员强制，普通用户可选）

## 监控任务
Task: `{id, name, group, category, part_number, product_name, color, capacity, stores:[{number,name,city}], mode:"instant"|"confirmed", repeat_interval_sec|null, channels:{bark_key?, webhooks?[], email?}, paused, expires_at, created_at}`

- `GET /api/tasks` → 列表（含每任务最新状态摘要）
- `POST /api/tasks` → 201（按 tier 校验任务数上限）
- `POST /api/tasks/batch` `{part_numbers[], store_numbers[], name_template}` → 门店×型号批量生成
- `PATCH /api/tasks/{id}`（改名/分组/暂停/有效期/渠道）
- `DELETE /api/tasks/{id}`

## 目录（公开）
- `GET /api/catalog/stores?refresh=0` → `[{number, name, city, province}]`（全国直营店，refresh=1 触发在线刷新）
- `GET /api/catalog/products?category=iphone|ipad|mac|watch` → `[{part_number, name, color, capacity, price_cny}]`

## 库存状态（六态，前端展示用）
state ∈ `available | unavailable | unknown | verifying | cooling | paused`
- `GET /api/tasks/{id}/states` → 按门店×配置的状态列表（含 pickupDisplay/storePickEligible 原始字段、updated_at）

## 通知
- `POST /api/notify/test` `{channel, target}` → 发送测试通知，返回成功/失败（链路测试）
- `GET /api/notifications?task_id=` → 通知历史
- 通知直达链接由后端生成：`https://www.apple.com.cn/shop/buy-iphone/...` 或购物袋 URL

## 历史与数据
- `GET /api/history/events?part_number=&store=&days=30` → 有货事件（活动日志）
- `GET /api/history/releases?days=7` → 放货记录
- `GET /api/analytics/ranking?days=1` → 全国榜单（城市放货排行）
- `GET /api/analytics/overview` → 数据分析摘要
- `GET /api/guide/purchase` → 到货购买指南（静态内容）
- `GET /api/stats/poll` → 上次查询/成功率/平均响应（按用户任务聚合）

## 配额与会员
- `GET /api/quota` → `{tier, push_used, push_limit, tasks_used, tasks_limit, refresh_interval_sec, period}`
- `GET /api/plans` → 四档说明（公开）

## 支付（爱发电）
- `POST /api/pay/afdian-webhook`（签名校验）→ 自动开通/续期会员，写 payments 表
- `GET /api/payments` → 当前用户付费记录

## 后台（/api/admin/*，需 admin 会话 + TOTP）
- `GET /api/admin/overview` → KPI：用户数/会员分布/今日推送/收入
- `GET /api/admin/traffic?days=30` → 访问流量（PV/UV，按日）
- `GET /api/admin/users?q=&tier=` → 会员列表/详情/改级
- `GET /api/admin/payments` → 付费记录
- `GET /api/admin/system` → 引擎状态/队列/限流/日志 tail
- `GET /api/admin/audit` → 管理员操作日志
- 所有写操作记 audit log。

## 限流
- 公开接口：60 req/min/IP；登录接口：5 次失败锁 IP 15 分钟；目录刷新：1 次/小时。
