# 第二轮产品逻辑审查 · C/D 维度（通知链路闭环 / 库存状态与展示一致性）

## 一、断裂

### C1-1. 配额"先扣后发"：通知发送失败照样扣配额，且 0 重试
- 依据：`engine.py:259` `_process_task` 里 `if self._check_quota(db, task):` 通过才 `_fire`；`_check_quota` 在 `engine.py:307` 执行 `usage.push_count += 1`（匿名任务 `_check_trial_quota` 同样先递增）。`Notifier.dispatch`（`notifier.py:128-174`）每个通道 try/except 单发一次，全仓库 grep `retry/backoff/tenacity` 零命中——失败只记 `status="failed"`，无重试。
- 场景复现：用户 Bark key 填错一位 → 到货触发 → 配额 -1 → 发送抛异常记 failed → 用户没收到任何东西，配额照扣。免费档月配额只有 5，一次到货就可能白烧。
- 成熟做法：DING果"通知按次消耗"对应实际送达的通知；常规做法是发送成功后扣减，或失败不扣 + 有限重试（3 次指数退避）。
- 修复建议：扣减移到 `dispatch` 返回后，按实际 `status="sent"` 条数扣减；或失败时回滚。至少加 3 次重试（30s/2m/10m），重试成功不重复扣。

### C1-2. 无 DING果"100次发不出任务自动结束"的对等机制
- 依据：`models.py` 的 `MonitorTask` 无连续失败计数字段；`engine.py` 无失败计数/自动暂停逻辑；`notifications` 表只写不读。
- 场景复现：用户 webhook URL 失效 → 每次到货：扣配额 → 发送失败 → 静默。任务永远显示"运行中"，用户以为正常，配额持续被烧。
- 成熟做法：连续 N 次失败 → 自动暂停任务 + 发一次兜底通知（邮件）。
- 修复建议：`MonitorTask` 加 `consecutive_failures`，全通道失败 +1、全成功清零；≥10 次自动 `paused=True` 并记 `kind="task_auto_paused"` 通知（走邮件兜底）；前端给 distinct 文案。

### C4-1. 通知直达链接是死链：`build_product_link` 拼出的 URL 实测 404
- 依据：`notifier.py:24-33` 生成 `https://www.apple.com.cn/shop/buy-iphone/{part_number}`（大写，如 `MJYC4CH/A`）。
- 实测验证：请求 `https://www.apple.com.cn/shop/buy-iphone/MJYC4CH/A` → HTTP 404；Apple 真实 SKU 页形态是 `https://www.apple.com.cn/shop/product/mjyc4ch/a`（`/shop/product/` + 小写），实测返回有效商品页。
- 场景复现：用户收到到货推送，点通知 → 404 → 自己去官网搜 → 错过抢购。这是 20 项功能明确承诺的"通知直达到货SKU页/购物袋"，核心卖点断裂。
- 修复建议：改为 `f"https://www.apple.com.cn/shop/product/{part_number.lower()}"`；"点通知跳购物袋"不可实现（Apple 购物袋是 cookie 会话，静态 URL 无法预填 SKU），文案改成"直达商品页"。

### C2/C3-1. 三个功能后端全写了，前端一个入口都没有：链路测试 / 通知历史 / 企微·钉钉·飞书配置
- 依据：后端 `notify.py:19-54` 有 `/notify/test`（6 通道全支持），`notify.py:57` 有 `/notifications` 历史；前端 `api.ts` 封装了 `notifyTest`/`notifications`，但全仓库 grep 没有任何页面调用。`AddMonitor.tsx:128-131` 建任务时 `channels` 只收 `bark_key` + `email`，企微/钉钉/飞书 webhook 前端无配置入口（`frontend/src` 全目录 grep `wecom/dingtalk/feishu` 仅命中 `api.ts:68` 类型声明）。
- 场景复现：用户想配企业微信群机器人——前端没地方填；想测试 Bark 通道——没按钮；推送失败想看原因——没页面。20 项功能里的"通知链路测试"对用户等于不存在。
- 成熟做法：DING果在任务配置里直接选通道 + 一键测试。
- 修复建议：必须补（优先级最高）：任务详情/Me 页加"通知渠道"配置区（Bark/企微/钉钉/飞书 webhook/邮件五通道表单 + 逐个"发送测试"按钮 + 最近通知记录列表含失败原因）。

## 二、不自洽

### C1-3. 配额扣减单位是"边沿事件 × 门店 × 配置"，但用户看到的是"月推送 X 次"
- 依据：`engine.py:259` 的 `_check_quota` 在 `for part / for store` 双重循环内（`engine.py:242-262`）逐个调用——3 门店任务一次到货烧 3 次配额；但 `dispatch` 把 Bark+webhook+邮件多通道扇出只算 1 次。
- 矛盾：门店维度是乘法、通道维度是"打包价"。用户按"一条通知=一次"理解"月推送 5 次"，实际 3 门店任务到货一次烧 3/5。DING果按实际发出条数扣。
- 修复建议：二选一并写进产品文案：(a) 按实际成功发送的通知条数扣（推荐，与 DING果对齐）；(b) 保持"事件制"但在配额页明确写"每次到货事件扣 1 次（多门店分别计）"。

### C2-1. 链路测试 ≠ 真实发送：测试消息没带直达链接
- 依据：真实发送 `notifier.py:156` 给 webhook 发 `body + f"\n{link}"`；测试 `notifier.py:197` 只发 `body`（无 link）。
- 修复建议：`test_channel` 的 webhook/email 分支同样附上测试链接。

### D2-1. Home 列表的"混合未知"被淹没
- 依据：`frontend/src/lib/api.ts:128-133` `summarizeTask`：无 available 时若"不全是 unknown"则整体显示 `unavailable`。
- 场景：5 家门店，3 家 unknown + 2 家 unavailable → 卡片显示灰色「无货」，unknown 被隐藏。详情页能分清（`StockStateBadge.tsx:20-30` 未知=琥珀虚线 vs 无货=灰色），但列表页语义丢失。
- 修复建议：混有 unknown 时整体文案改为「部分未知」或加琥珀小点提示。

### 前后端 `webhooks` 类型打架（现在无害，但随时会炸）
- 依据：前端 `api.ts:68` 声明 `webhooks?: string[]`；后端 `notifier.py:159-161` 要求 `[{url, platform}]` 对象数组；`schemas.py:61` 的 `channels` 只是裸 `dict`，无校验。
- 修复建议：后端 `schemas.py` 给 channels 加 Pydantic 模型校验（platform 枚举 + url 格式），非法直接 400。

### D4-1. 冷却只标"当前 chunk"，同轮其他任务保持旧态
- 依据：`engine.py:212-217` 限流时 `_enter_cooldown(db, tasks, chunk)` 只把本组本 chunk 写 `cooling`（`engine.py:367`），随后 return 跳过本轮其余组——那些组保持上轮旧状态（可能是几小时前的"有货"）。
- 修复建议：冷却 badge 旁显示"数据可能过期"；或冷却时所有任务 `updated_at` 冻结展示为"X 分钟前（冷却中）"。

## 三、对标差距
1. **"预计送货日期"功能完全缺失** —— 必须补。全仓库 grep `预计/送货/delivery` 零命中；`pickup_search_quote` 是 Apple 自提取货文案且前端从未展示（`TaskDetail.tsx:84` 只渲染 `pickup_display`）；`apple_client.py` 没取任何配送日期字段。这是用户 20 项功能清单第 20 项，上线前必须补（或明确砍掉并告诉用户）。
2. **通道健康状态展示** —— 必须补。无 fallback，但用户发现通道挂掉的唯一途径是调裸 API。建议 Me 页加"通道健康"卡（各通道近 7 天成功率 + 最后失败原因）。
3. **失败降级（自动转邮件）** —— 可以晚点。Bark/企微挂时自动用邮件重发，DING果是微信兜底。当前无任何降级。
4. **通知按"实际发送条数"扣费口径** —— 可以晚点（见 C1-3）。
5. **高峰期限流/优先通道可视化** —— 不用做。已有 Pro 优先轮询（`engine.py` `_due_tasks` 按 tier 排序），属内部机制。

## 四、明确 OK 的部分
- **D1 六态映射**：后端三态（`apple_client.py:64-69`）→ `cooling` 引擎限流写入（`engine.py:367`）→ `verifying`/`paused` 展示层换算（`tasks.py:44-55`）→ 前后端一致。`API_CONTRACT.md` 声明"state 是展示六态、不是 Apple 原始字段"与实现相符。
- **D4 失败链条不误导**：403/541 → `AppleRateLimitError` → 「冷却中」；其他异常 → `AppleError` → 「未知」；`classify` 兜底 unknown。**没有任何路径把失败写成 `unavailable`**，"失败≠无货"铁律守住了。
- **C2 测试不扣配额**：`test_channel`（`notifier.py:176-208`）不碰 `_check_quota`，只受每日 5 次防滥用限制（`notify.py:16`）；失败原因写入记录并返回——逻辑对，只是没前端。
- **短信通道诚实**：`tiers.py` 注释明确 sms 未接入前不向任何档位开放。

## 修复优先级
1. **P0**：修 `build_product_link` 死链（改 `/shop/product/` + 小写，一行代码，实测验证过）。
2. **P0**：补前端"通知渠道配置 + 链路测试 + 通知历史"入口。
3. **P0**：配额"先扣后发"→按实际发送成功扣减；加失败重试。
4. **P1**：连续失败自动暂停任务 + 兜底通知。
5. **P1**：补"预计送货日期"或正式砍掉；`webhooks` 加后端 Pydantic 校验。
6. **P2**：通道健康看板、失败自动转邮件、配额口径文案、Home 混合 unknown 提示、冷却 stale 提示。
