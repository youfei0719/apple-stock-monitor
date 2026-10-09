# 产品/门店数据标准

## 产品目录标准

1. **完整矩阵**：每个机型的容量 × 颜色必须是全组合，不允许出现"选了容量+颜色却没有对应 SKU"的情况。
2. **Part Number 唯一**：所有产品的 part_number 必须全局唯一。
3. **价格一致**：同一机型 + 同一容量，价格必须一致（不因颜色不同而不同）。
4. **品类覆盖**：iphone / ipad / mac / watch 四个品类，每个品类要么有至少一个机型，要么在前端明确标注"产品库建设中"及原因。
5. **字段完整**：每个产品必须有 part_number、name、color、capacity、price_cny、category，缺一不可。

## 门店目录标准

1. **字段完整**：每个门店必须有 number、name、city、province。
2. **编号唯一**：store number 全局唯一。
3. **种子店定位**：SEED_STORES 是降级 fallback，不是全量数据。全量靠 `_do_refresh_stores` 从 Apple API 按 SEED_CITY_ANCHORS 抓取。种子店只需覆盖核心城市（深圳/上海/杭州/宁波），保证首次启动可用。

## 验证

运行 `python3 scripts/validate_catalog.py` 检查以上所有规则（不依赖 fastapi，可在 CI 跑）。
