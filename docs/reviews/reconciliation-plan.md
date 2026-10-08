# 爱发电订单对账 job · 设计方案（首版：文档）

日期：2026-10-09 · 状态：**只留文档，不实现**

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
   - 标记 `status='refunded'`/`'cancelled'`，**首版不自动降级**（避免爱发电状态误报误伤），发告警由人工确认后降级 + 记录 audit。
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
