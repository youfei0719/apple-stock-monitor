# 爱发电订单对账 job · 设计方案

日期：2026-10-09 · 状态：**首版=管理端动作已实现；定时对账 job 仍只留文档（TODO）**

## 首版已实现的对账承接动作（第三轮审查 D3/D4，2026-10-09）

管理端（/api/admin/payments/*，写操作全部记 audit log）：

| 动作 | 接口 | 效果 |
|---|---|---|
| 认领 | `POST /api/admin/payments/{id}/claim {"user_id"}` | 绑定用户 + 按订单档位开通 30 天（到期/锚点同步）+ 回填 `Payment.user_id`；若订单为 `amount_mismatch`，成功后置 `status='resolved'` 并递减待处理计数（从待处理队列移除） |
| 不予开通直接关闭 | `POST /api/admin/payments/{id}/close` | `status='resolved'`（移出待处理队列，待处理计数 -1），不绑定用户、不开通档位；人工定夺"不处理"时用 |
| 标记退款 | `POST /api/admin/payments/{id}/refund` | `status='refunded'`；`revenue_cny` 统计只计 `paid`（已自动排除）；清空该用户 `tier_expires_at` 并降回 `free`（`pending_tier` 同步清空） |

待处理计数 key：`system_config` 的 `payments_amount_mismatch_pending`（与 pay.py webhook 落库的计数器同源，认领/关闭成功时 -1，下限 0）。

对账流程（首版，人工驱动）：
1. `GET /api/admin/overview` 看 `pending_payments`（unclaimed / amount_mismatch 计数）；
2. `GET /api/admin/payments?claim_status=unclaimed` 或 `?status=amount_mismatch` 拉队列；
3. remark/爱发电商家后台核实归属与金额；
4. 钱货相符 → claim（自动开通 + 出队）；钱已到但不该开通 → close（出队）；爱发电已退款 → refund（降回 free + revenue 自动排除）。

长期 TODO（定时对账 job）：爱发电订单 API 以 `AFDIAN_USER_ID` + `AFDIAN_PARAMS_TOKEN` 鉴权，每 6 小时拉近 7 天订单，与本站 `Payment` 按 `order_id`/金额/状态三层比对；异常落库 `reconciled`/`amount_mismatch`/`refunded`/`cancelled` 并 Bark 告警。爱发电商家后台就绪后实测接口形状再实现。

## 解决的问题（对应 round2 评审）

- 断裂-2：用户在爱发电取消续费/退款成功，本站无感知（会员照用）。
- 断裂-15：金额强校验失败（400 拒绝）后只有 `log.error`，无待处理队列。
- 断裂-17：webhook 丢失无对账/补单机制；人工补单不设到期时间。
- 断裂-3：签名算法未经官方对拍——对账 job 是独立于 webhook 的第二条真相来源。

## 数据源

爱发电开放平台订单查询接口（社区已知形态：`POST https://afdian.com/api/open/query-order`，需 `user_id` + `params_token` 鉴权）。
⚠️ 具体请求字段形状**以用户爱发电商家后台就绪后实测为准**，不要凭社区文档硬编码。鉴权参数走服务器环境变量，与 webhook 的 `AFDIAN_TOKEN` 分开配置。

## 比对字段

| 爱发电订单字段 | 本站 `Payment` 字段 | 比对逻辑 |
|---|---|---|
| `out_trade_no` | `order_id` | 主键。爱发电有、本站无 → **webhook 丢失** |
| `total_amount`（分） | `amount_cny` | 不一致 → **金额异常** |
| `plan_id` | `plan` | 不一致 → 档位配置漂移（爱发电改价/本站 `EXPECTED_AMOUNT_FEN` 滞后） |
| 订单状态（成功 / 退款 / 取消） | `status` | 爱发电已退款/取消而本站仍 `paid` → **坏账** |
| `create_time` | `created_at` | 时间窗口对齐，排查重复回调 |

比对顺序：先主键对齐 → 再金额 → 再状态。金额异常和状态异常都以前者（主键）为前提。

## 频率与范围

- **每 6 小时**跑一次（爱发电订单 API 有调用频率限制；webhook 仍是实时主链路，对账只是兜底）。
- 每次拉取**近 7 天**订单 + 上次对账时间戳游标（增量）。
- 低峰时段跑（避开 08:00–10:00 推送高峰，不与引擎抢 Apple 请求预算）。

## 异常处理

1. **webhook 丢失**（爱发电有、本站无）
   - 自动落库 `Payment(status='reconciled')`，按 remark 规则关联用户；
   - remark 可关联 → 自动开通/续期（到期规则同 webhook：`max(tier_expires_at, now)+30 天`）+ Bark 告警"自动补单 X 笔"；
   - remark 关联不上 → 进**待认领队列**（admin 可见），Bark/邮件告警，人工认领。
2. **金额异常**（`total_amount` ≠ 档位期望）
   - 落库 `status='amount_mismatch'`，**不自动开通**；Bark/邮件告警 + admin 待处理视图（断裂-15 的修复落点）。
3. **爱发电已退款/取消、本站为 paid**
   - 标记 `status='refunded'`/`'cancelled'`，**不自动降级**（避免爱发电状态误报误伤）；人工用 `POST /api/admin/payments/{id}/refund` 确认后降回 free + 记录 audit。
4. **爱发电 API 本身失败**
   - 本轮跳过，指数退避，记 warning 日志；绝不阻塞主链路、不抛错惊动用户。

## 状态值扩展（实现时改 `models.py`）

`Payment.status` 现只有 `'paid'`。对账需要：`'reconciled'`（对账补单）、`'amount_mismatch'`（金额异常）、`'refunded'`（已退款）、`'cancelled'`（已取消）。`my_payments` / admin payments 的展示文案同步加。

## 实现位置（实现时）

- `backend/app/services/reconcile_afdian.py`：拉取 + 比对 + 落库（只写 Payment/User，不碰通知链路）。
- cron job（与 breaking-news-watch 同类）：每 6 小时触发。
- 配置：`AFDIAN_USER_ID`、`AFDIAN_PARAMS_TOKEN`（服务器环境变量，600 权限）。

## 首版边界（明确不做）

- 不自动降级（退款/取消只标记 + 告警，人工确认）。
- 不回写爱发电（只读比对）。
- 不做实时对账（webhook 仍是实时路径）。
