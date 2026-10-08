# 预计送货日期（20 项功能第 20 项）· 调研结论：正式砍掉

日期：2026-10-09 · 结论：**从功能清单正式砍掉，不做**

## 调研过程

实测 Apple 中国官网两个库存接口（2026-10-09，本机直接请求）：

1. `GET /shop/fulfillment-messages?pl=true&mt=regular&parts.0=MJYC4CH/A&location=深圳`
- 2026-10-08 曾实测 200；本轮多次请求（含 `store=R484` 参数版、后端 provider 同款参数）全部 **HTTP 541**（128KB HTML 限流页）。与 `apple_client.py` docstring 记录一致："fulfillment-messages 老接口备用：中国区长期 541"。
- 未能拿到 JSON 响应体，无法确认该接口是否含送货日期字段。

2. `GET /shop/retail/pickup-message?parts.0=MJYC4CH/A&location=深圳` → 200（532 字节），返回**只有本地化文案**（"查看更多时段""距离最近的零售店今天不可取货"等），无门店数据、无日期字段。

3. `GET /shop/retail/pickup-message?parts.0=MJYC4CH/A&store=R484` → 200（6178 字节），返回 `body.stores[0]`（深圳益田假日广场门店信息：storeNumber/storeName/邮箱/营业时间）+ `availability: {"isComingSoon": false}`。**全响应逐键检查：无任何送货日期/时间窗字段**。无 `deliveryDate`/`shipDate`，只有自提相关的 `pickupDisplay`/`storePickEligible`/`pickupSearchQuote`（后端 `_parse_stores` 已取）。

## 砍掉依据

- **数据源层**：本项目生产走的是 `pickup-message`（自提库存接口，门店取货场景），其 200 响应里**根本没有送货日期字段**——送货日期是官网配送下单链路的信息，自提库存 API 的响应结构不含它。要展示送货日期需要走官网商品页配送报价链路（`fulfillment-messages` mt=regular 配送模式），该接口从本环境长期 541，生产侧不可依赖。
- **产品场景层**：本项目卖的是"门店有货→立刻自提"，送货日期（官网配送 3–5 天到货）与"抢自提"场景弱相关，且 DING果的核心也是有货即通知，送货日期不属于对标必备。
- **投入产出**：为一条弱相关信息新增一个长期 541 的备用接口链路 + 解析 + 缓存 + 前端展示，ROI 不值得；541 接口还可能拖慢引擎或触发全站冷却。

## 后续

- 20 项功能清单从 20 项变为 19 项（需同步用户：此为他拍板锁定的清单，建议由协调员在早会上明确告知）。
- 本文件中"送货日期"仅出现于历史评审结论标注，其余提及文件已改写见下方。
- 若将来 Apple 恢复 `fulfillment-messages` 稳定访问或用户坚持要，重开此功能只需：pickup-message 响应里找 `pickupSearchQuote`（Apple 自提文案，含"今天可取"类时效措辞，`TaskDetail.tsx:84` 当前未渲染）——这是唯一已在手的数据里最接近"时效"的字段。

## 提及此功能的文件改写记录（2026-10-09，B 出口）

- `docs/reviews/round2-product-logic.md`：一句话总评、"三、对标差距-必须补-3"、"五、优先级-10" 三处均标注 ****。
- `docs/reviews/review-round2-CD.md`："三、对标差距-1"、优先级列表 "P1" 条均标注 ****。
- 仓库内业务代码（`backend/app`、`frontend/src`、`docs/API_CONTRACT.md`）grep `预计/送货/delivery` 零命中，无需改。
- 20 项功能清单原文（用户 2026-10-09 00:41 拍板，记于当日日志）中的"预计送货日期"条目：请协调员在与用户对账时明确划掉并告知。
