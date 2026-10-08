# Apple 库存监控 · 第二轮产品逻辑审查（完整清单）

**一句话总评**：配额与会员两套体系各自都能跑，但**会员没有到期、配额没有护栏、任务没有终点**——三个"没有"都是闭环上的洞；支付闭环多处漏钱；20 项功能里"通知直达到货SKU页"是死链、"预计送货日期"根本没做。

---

## 一、断裂（上线前必须修，用户会卡住/丢钱/丢数据）

### 会员与收入（地基）

**断裂-1：会员到期永不降级——一次付费，终身会员**
- 依据：`pay.py:124-126` 只写 `tier` + `tier_expires_at`；全库无任何降级逻辑（grep `tier_expires_at` 仅写入与展示）；`deps.py:get_current_user` 不校验过期；engine 按 `t.user.tier` 调度（`engine.py:162`）。
- 场景：用户付 ¥19 开标准版，30 天后依然是标准版——100 次/月推送、30 秒刷新、历史权限永久保留。订阅商业模式地基是空的。
- 成熟做法：每日低频 job 扫 `tier_expires_at < now AND tier != 'free'` → 降为 free + 记审计 + 通知用户；到期前 3 天/1 天续费提醒；超限任务宽限期后暂停。
- 修复：加 `effective_tier(user)`（过期按 free 算）替换所有 `tier_of(user.tier)` 调用点，或独立 sweep cron；前端展示到期时间（`/quota` 已返回 `tier_expires_at`，`api.ts` 根本没定义该字段）。

**断裂-2：取消续费 / 退款没有任何承接**
- 依据：webhook 不解析任何事件类型字段（pay.py 全函数无 type/event 判断）；`Payment.status` 只有 `'paid'` 一种值（models.py:166）。
- 场景：用户在爱发电取消自动续费 → 本站无感知；申请退款成功 → 本站订单仍是 paid、会员照用——钱退了，服务照享。
- 修复：短期退款走人工（Payment.status 加 refunded/cancelled + admin 标状态 + 手动降级）；长期加对账 job（拉爱发电订单 API 比对）。

**断裂-3：验签算法未经官方对拍，注释明写"联调前勿用"**
- 依据：pay.py:1-16 docstring。上线后真实回调验签失败 → 全部 403 → 用户付钱、一个都开不了通。
- 修复：上线 blocker，用爱发电后台真实回调对拍签名（需用户爱发电就绪后）。

### 配额护栏

**断裂-4：持续提醒可一夜烧完月度配额，无任何护栏**
- 依据：`schemas.py:60,81`（`repeat_interval_sec` 无 `ge` 下限）；前端 `AddMonitor.tsx:134-136`（`ri > 0` 就接受，1 秒都行）；`engine.py:239-249` 到期即 `notify=True`；`engine.py:251-256` 每次 `_fire` 扣 1 次。
- 场景：标准会员（100 次/月）监控持续有货 SKU，`repeat_interval_sec=60` → 100 分钟烧完月度配额，之后整月所有任务静默。3 门店按 (store,part) 独立计数烧得更快。DING果卖次数包、用户对次数有强感知；我们包月固定次数 + 用户可自配间隔无限加速消耗，无换算公式/预计烧完提示/间隔下限/80% 预警。
- 修复：后端 `repeat_interval_sec` 加 `ge=3600`；设置时显示"预计月消耗 ≈ X 次"；80%/100% 推送预警（不扣配额）；耗尽自动 `paused=True` 并任务卡标红。

**断裂-5：配额"先扣后发"——发送失败照样扣配额，且 0 重试**
- 依据：`engine.py:259` 先 `_check_quota`（`engine.py:307` 执行 `push_count += 1`）再 `_fire`；`Notifier.dispatch`（`notifier.py:128-174`）每通道单发一次，全仓库 grep `retry/backoff/tenacity` 零命中。
- 场景：Bark key 填错一位 → 到货触发 → 配额 -1 → 发送失败记 failed → 用户什么都没收到。免费档月配额只有 5，一次到货就可能白烧。
- 修复：扣减移到 `dispatch` 返回后按实际 `status="sent"` 条数扣减（或失败回滚）；失败加 3 次重试（30s/2m/10m），重试成功不重复扣。

**断裂-6：无 DING果"100次发不出任务自动结束"的对等机制**
- 依据：`MonitorTask` 无连续失败计数字段；`engine.py` 无失败计数/自动暂停；`notifications` 表只写不读。
- 场景：webhook URL 失效 → 每次到货扣配额 → 发送失败 → 静默。任务永远显示"运行中"，用户以为正常，配额持续被烧。
- 修复：`MonitorTask` 加 `consecutive_failures`，全通道失败 +1、全成功清零；≥10 次自动 `paused=True` + 记 `kind="task_auto_paused"` 通知（走邮件兜底）；前端给 distinct 文案。

### 任务生命周期

**断裂-7：任务过期后"无声死亡"，且显示过期旧状态误导用户**
- 依据：`engine.py:157` 过期任务排除出轮询后无归档/通知/清理；`tasks.py:43-51`（`_display_state` 六态里没有 expired）；前端 Home 无续期按钮，TaskDetail 无操作按钮。
- 场景：任务 `expires_at` 一到 → 停止轮询，但躺在列表里显示最后一次轮询的旧状态（如 `available`）——用户误以为"还在监控/有货"。匿名 trial 任务强制 24h 过期，过期后永久堆积在 DB。
- 修复：加 `expired` 展示态；列表分"监控中/已过期"；过期前 3 天/1 天提醒；一键续期；trial 过期任务 7 天后物理删除。

**断裂-8：重复任务 → 重复通知 + 重复扣配额**
- 依据：`create_task`/`batch_create` 均无 `(user, part, stores)` 查重；引擎 `_group_tasks`（`engine.py:176-186`）合并了 Apple 请求，但 `_process_task` 按 task 独立 `_fire`、独立扣配额（`engine.py:210-260`）。
- 场景：批量生成不小心提交两次（batch 无幂等）→ 同一次到货发 2 条通知、扣 2 次配额。标准会员 100 次/月直接腰斩。
- 修复：创建时对 `(user_id, part_number, store_numbers集合)` 冲突检测，409 提示"已存在相同任务"。

**断裂-9：体验版 1 次推送用完后，任务"假活"**
- 依据：`engine.py:285-300` 超限返回 False → `engine.py:257-259` 只记 skipped；`_due_tasks`（`engine.py:153-173`）只看 `paused/expires_at`，配额耗尽的任务照样每 5 分钟轮询 Apple API。
- 场景：匿名用户 1 次推送用完 → 任务不暂停不降级继续烧 Apple API；用户完全感知不到（`/quota` 要求登录；skipped 记录 `user_id=None` 查不到；前端仍显示"正常"）。
- 修复：trial 配额耗尽 → 自动 `paused=True`，前端任务卡显示"体验推送已用完，去注册/升级"。

**断裂-10：登录后体验任务"消失"，后台继续跑、关不掉**
- 依据：匿名任务 `user_id=None` + `device_id` 绑定（`tasks.py:148-149`）；`list_tasks`（`tasks.py:112-121`）登录后永远走 user 分支；`_get_owned`（`tasks.py:216-225`）登录态操作 device 任务直接 403。
- 场景：用户体验后注册登录 → 旧任务后台继续每 5 分钟轮询（烧 Apple API），用户看不见、暂停不了、删除不了。
- 修复：注册/登录时把同 device 的匿名任务迁移绑定到 `user_id`（一次性认领），或登录后提示"发现体验任务，是否接管"。

**断裂-11：长期无货的僵尸任务永无出路**
- 依据：无任何"连续 N 轮无货 → 提示/自动结束"机制；`expires_at=None` 的任务可永久轮询。
- 场景：冷门 SKU 任务永久占用任务名额（`quota.py:28-30` 全量计数）、永久消耗 Apple API。
- 修复：连续无货 30/60/90 天各发一条提醒（不扣配额）；加"自动结束"开关（默认开：连续 90 天无货自动暂停并通知）。

### 通知链路

**断裂-12：通知直达链接是死链（实测 404）**
- 依据：`notifier.py:24-33` 拼 `https://www.apple.com.cn/shop/buy-iphone/{part_number}`（大写）；实测 `…/buy-iphone/MJYC4CH/A` → HTTP 404；Apple 真实 SKU 页是 `https://www.apple.com.cn/shop/product/mjyc4ch/a`（`/shop/product/` + 小写），实测有效。
- 场景：用户收到到货推送，点通知 → 404 → 自己去官网搜 → 错过抢购。这是 20 项功能明确承诺的"通知直达到货SKU页/购物袋"。
- 修复：改为 `f"https://www.apple.com.cn/shop/product/{part_number.lower()}"`；"点通知跳购物袋"不可实现（Apple 购物袋是 cookie 会话，静态 URL 无法预填 SKU），文案改成"直达商品页"。

**断裂-13：前端无通知渠道配置 / 链路测试 / 通知历史入口**
- 依据：后端 `notify.py:19-54`（`/notify/test`，6 通道全支持）、`notify.py:57`（`/notifications` 历史）全有；前端 `api.ts` 封装了但无任何页面调用；`AddMonitor.tsx:128-131` 只收 `bark_key` + `email`，企微/钉钉/飞书 webhook 前端无配置入口。
- 场景：用户想配企微群机器人——没地方填；想测试 Bark——没按钮；推送失败想看原因——没页面。20 项功能里的"通知链路测试"对用户等于不存在。
- 修复：任务详情/Me 页加"通知渠道"配置区（五通道表单 + 逐个"发送测试"按钮 + 最近通知记录列表含失败原因）。

### 支付坏账链路

**断裂-14：前端付费指引写错了，持续产生坏账**
- 依据：`Me.tsx:170-172` "爱发电订单号请与账号邮箱保持一致，以便自动开通"。事实：`out_trade_no` 由爱发电生成，用户改不了；后端实际认的是 **remark**（pay.py:86-97：纯数字→user_id，含 @→email）。
- 场景：用户照着做（做不到），remark 留空 → `Payment.user_id=None` → 付了钱没开通。
- 修复：文案改为"赞助时在备注/留言中填写你的用户 ID #123 或注册邮箱"；开通按钮跳 `https://afdian.com` 首页（Me.tsx:167）应配成自家赞助页 URL（`AFDIAN_PAGE_URL`）。

**断裂-15：金额强校验 + 失败无通知 = 钱货两空黑洞**
- 依据：pay.py:109-119，`amount_fen` 必须精确等于 1900/3900，否则 400 拒绝；失败只有 `log.error`，无管理员/用户通知、无待处理队列。
- 场景：用户用优惠券/自定义金额支付、或档位调价后配置滞后 → 爱发电已扣款（不退），本站 400 拒绝 → 用户无会员，管理员不知情，只能等投诉。
- 修复：`amount_mismatch` 时仍落库（status='amount_mismatch'，尽量关联 user_id），admin 待处理视图 + Bark/邮件告警。

**断裂-16：未认领订单在后台不可识别归属 → 无法补单**
- 依据：`admin.py:152-186 list_payments` 不返回 `raw_payload`（remark 在里面）也不返回 email。
- 场景：用户 remark 填错 → 后台看到一笔 `user_id=—` 的 ¥19 → 不知道是谁的钱 → 无法补单。
- 修复：admin payments 接口返回 remark（从 raw_payload 提取）+ 列表页"待认领"筛选 + 绑定用户接口。

**断裂-17：webhook 丢失无对账/补单机制；人工补单不设到期时间**
- 依据：无定时对账；`AdminUserPatchIn` 无 `tier_expires_at` 字段（schemas.py:110-113），`patch_user` 只改 tier（admin.py:123-151）。
- 场景：webhook 丢了 → 用户投诉 → admin 手动改 standard → 该用户永久 standard（无到期），与正常付费用户（30 天）不一致；Payment.user_id 仍是 None，账实分离。
- 修复：`AdminUserPatchIn` 加 `tier_expires_at` 可填项；补单时联动回填 Payment.user_id；长期加对账 job。

### 数据与转化

**断裂-18：「放货记录」tab 对免费用户是 403 报错页，不是升级引导**
- 依据：`history.py:66-69`（`tier_of()["history"]` 为 False 直接 403）；`History.tsx:56` 只做 `catch → ErrorState`，无档位判断、无升级入口。
- 场景：免费用户点"放货记录" → 红色错误"完整历史数据需要标准版及以上" → 没有"去开通"按钮，付费转化漏斗断了。
- 修复：前端捕获 `code === "tier_required"` 时渲染升级卡片（跳转 Me 页付费区）。

**断裂-19：「全国榜单」名不副实——实际是"我的放货城市分布"**
- 依据：`analytics.py:18-30`（`_event_rows` 固定过滤 `Notification.user_id == user.id`），`ranking()` 按该用户自己的有货通知聚合城市；前端 tab 名"全国榜单"、标题"城市放货排行 · 今日"。
- 场景：只监控深圳 3 家店的用户，榜单永远只有"深圳"一行。DING果的是真·多用户聚合的全国城市榜。名称即承诺，名不副实是信任问题。
- 修复：短期改名 + 空状态文案诚实化；长期做全站匿名聚合。

### 反薅与成本

**断裂-20：无任何全局/单用户 Apple 请求预算，一个重度用户可拉全站下水**
- 依据：`config.py:68-71`（trial/free=300s、standard=30s、pro=10s）；`engine.py:187-196`（每 chunk 1 次 Apple 请求）；`engine.py:358-372`（541/403 后 `wait=min(60*2^level,3600)` 最高 1 小时）；tick 单线程串行无并发上限（`ENGINE_MAX_WORKERS=4` 定义了但 engine.py 零引用）。
- 场景：1 个 pro 用户 30 个任务 × 每 10 秒 = 3 req/s；10 个这样的用户 → Apple 回 541 → `_enter_cooldown` 把本轮所有 due tasks（含别人的付费任务）冷却，全站最高 1 小时停摆。单个用户的成本被全站买单。
- 修复：`_due_tasks` 加 per-user 本轮请求计数，超限跳过记 skipped；加全局每分钟上限，超限时 pro 优先、trial/free 降速。

**断裂-21：单任务门店数无上限，是成本放大器**
- 依据：`schemas.py:58`（`TaskCreateIn.stores` 只有 `min_length=1`，无 `max_length`）；1 任务 N 门店 = `ceil(N/10)` 次 Apple 请求。
- 场景：pro 用户 30 任务 × 1000 门店 = 每 10 秒 3000 次请求 → 直接打爆限流，触发断裂-20 的全站冷却。
- 修复：`stores` 加 `max_length`（如 20/任务）；engine 侧超预算截断。

**断裂-22：邮箱验证未实现 → 可无限注册免费账号**
- 依据：`auth.py:59-64` TODO 明说邮箱验证未实现，注册直接开通（`auth.py:70` 限 `5次/小时/IP`）；匿名 trial 靠客户端自报 `X-Device-Id`（`tasks.py:119,132` 可伪造）+ `20次/小时/IP`。
- 场景：假邮箱 + 代理 = 无限免费账号（每号 3 任务 + 5 推送/月）；换 IP + 换 device_id = 无限 trial 身份。
- 修复：上线前必须补邮箱验证；trial 加 IP 段维度限流可以晚点。

**断裂-23：`tiers.py` 的 channels 字段是装饰品，付费墙漏了个洞**
- 依据：`tiers.py` 声明 trial=`["page"]`、free/standard/pro=`["email"]`；但 `notifier.py:140-147` 的 `dispatch` 直接按任务 `channels` 字典发送，全仓库无一处校验 tier 通道。
- 场景：免费用户在任务里配 `bark_key` 照样收到 Bark 推送。
- 修复：`dispatch` 入口按 `tier_of(user.tier)["channels"]` 过滤，或任务创建/更新时校验合法性。

---

## 二、不自洽（前后矛盾、语义含糊）

1. **降级立即生效，用户当场丢失已付费权益**：`pay.py:120-127` 无论升降级都当场改写 tier。pro 用户剩 20 天降到标准 → 当场失去 pro 权益，不折算不退款。成熟做法：升级立即生效、降级到期生效（记 `pending_tier`，到期 job 切换）。
2. **会员周期（购买日+30天）vs 配额周期（自然月 UTC）错位**：会员 `timedelta(days=30)`；配额 `engine.py:271` 自然月 1 号 0 点 UTC（= 北京时间 8 点清零）。10 月 25 日付费 → 11 月 1 日配额重置（多拿 1/4 个月）；10 月 31 日付费 → 10 月配额只有 1 天可用。二选一：配额改购买日对齐的 30 天滚动，或保持自然月但明确标注。
3. **配额口径含糊**：一次 `_fire` 扣 1 次（`engine.py:251-256`），但 3 门店任务一次到货在循环内扣 3 次（`engine.py:242-262`），而 Bark+webhook+邮件多通道扇出只算 1 次。门店维度是乘法、通道维度是"打包价"，用户按"一条通知=一次"理解必然困惑。明确口径并写进产品文案（推荐与 DING果对齐：按实际发送条数）。
4. **删除任务后，通知历史的 part_number 变空**：`db.delete(task)`（`tasks.py:248-252`），`StockState` 级联删除，`Notification.task_id` 外键 `ondelete="SET NULL"` → 历史里出现无 part_number、无门店名的"幽灵"通知。修复：`Notification` 加 `part_number` 冗余快照字段。
5. **暂停的任务占不占配额——代码自洽但未向用户说明**：暂停不轮询/不触发/不扣配额（`engine.py:156` ✓），但任务名额照占（`quota.py:28-30`）。前端 nowhere 提到"暂停仍占名额"。修复：任务卡暂停态加 hint。
6. **"完整历史"档位语义含糊**：门控只在 `/history/releases`；`/history/events`（30 天）、`/analytics/ranking`、`/analytics/overview`、`stats/poll` 全部无门控。`tiers.py` 只定义 `history: True/False`，没定义"完整"指什么。定语义并写进 `docs/API_CONTRACT.md`。
7. **降级后历史数据是"隐藏"（实现正确），但降级路径本身不可达**：无删除 notifications 的逻辑，门控只是 403 隐藏——成熟做法 ✓，但 `tier_of()` 不看过期且到期降级不存在，所以是死代码路径。随断裂-1 一起修。
8. **手动改级 vs webhook 开通：到期时间语义不一致**：webhook 设 `+30 天`；admin 手动改级不碰 `tier_expires_at`。随断裂-17 的补单入口修。
9. **"体验"档在会员页展示但注册用户拿不到**：`GET /plans` 返回 4 档含 trial（price 0）；实际注册默认 `free`，trial 只用于未登录匿名任务。修复：plans 不返回 trial，或标注"未登录体验"。
10. **Home 列表的"混合未知"被淹没**：`summarizeTask`（`api.ts:128-133`）无 available 且"不全是 unknown"时整体显示 `unavailable`。5 家门店 3 家 unknown + 2 家 unavailable → 卡片灰色「无货」，unknown 被隐藏（详情页区分做得不错：琥珀虚线「未知」vs 灰色「无货」）。修复：混有 unknown 时显示「部分未知」或加琥珀小点。
11. **链路测试 ≠ 真实发送**：真实发送 webhook 带 `body + link`（`notifier.py:156`），测试只发 `body`（`notifier.py:197`）。测试通过不能证明生产消息一定能发出。修复：测试分支同样附链接。
12. **前后端 `webhooks` 类型打架**：前端 `api.ts:68` 声明 `string[]`；后端 `notifier.py:159-161` 要求 `[{url, platform}]`；`schemas.py:61` 的 `channels` 是裸 `dict` 无校验。哪天前端按自己类型传了，`wh.get("url")` 直接 AttributeError。修复：后端加 Pydantic 模型校验，非法 400。
13. **冷却只标"当前 chunk"，同轮其他任务保持旧态**：`engine.py:212-217` 只把本组本 chunk 写 `cooling`，随后 return 跳过其余组——那些组保持几小时前的旧状态（可能是"有货"）。修复：冷却 badge 旁显示"数据可能过期"。
14. **`ENGINE_MAX_WORKERS=4` 是撒谎的配置**：`config.py:64` 定义，`engine.py` 零引用；实际单线程串行。pro 的 10s SLA 在任务稍多时被静默打破——前台展示"10s"是承诺，后端交付随缘。修复：实现 worker 池或删配置；tick 时长 > 最小间隔时记 warning。

---

## 三、对标差距（DING果有/强，我们没有/弱）

### 必须补
1. **高峰模式开关**：DING果"新品期免费用户禁查/高峰期优先"；我们全站冷却纯被动且连付费用户一起停。至少一个 SystemConfig 手动开关：高峰模式下 trial/free 间隔拉长、catalog 后台刷新暂停、pro 优先。
2. **配额预警 + 耗尽自动暂停**：DING果通知按次消耗、剩余次数强感知、100 次发不出自动结束；我们剩余次数只在"我"页小字展示，无预警、无自动结束。
3. **"预计送货日期"**：用户 20 项功能第 20 项，全仓库 grep `预计/送货/delivery` 零命中；`apple_client.py` 没取任何配送日期字段。上线前必须补（或正式砍掉并告诉用户）。
4. **通道健康状态展示**：用户发现通道挂掉的唯一途径是调裸 API。Me 页加"通道健康"卡（各通道近 7 天成功率 + 最后失败原因）。

### 可以晚点
5. **微信通知**：DING果核心特权；我们只有 Bark/企微/钉钉/飞书/邮件（sms 占位未实现但 tiers.py 已诚实不开放 ✓）。微信生态接入成本高，先保证 Bark+企微可用。
6. **全平台城市榜单**：需先有全站数据沉淀；注意隐私口径。
7. **失败降级（自动转邮件）**：Bark/企微挂时自动用邮件重发一次；DING果是微信兜底。
8. **后台运营四件套**：封禁/解封用户、手动补配额、全局公告、手动触发轮询（客诉/坏账否则只能 DB 直改；审计日志真实但只覆盖唯一的写操作 `patch_user`）。
9. **3 秒刷新档**：先验证 Apple 接口扛不扛得住 10s 高频再说。
10. **冷启动种子数据**：上线前用官方账号跑一批真实监控任务攒种子，或诚实标注"数据积累中 · 已收录 N 条"；数据保留/归档策略写进运维手册。

### 不用做
- 新品期免费用户禁手动查询：首版无手动查询入口，免费档靠 5 次/月 + 300s 间隔限流。
- 通知按次消耗、历史数据门控：已对齐 DING果 ✓。

---

## 四、已验证 OK 的部分（跟踪过完整链条，无问题）

- **六态映射前后端一致**：后端三态（`apple_client.py:64-69`）→ `cooling` 引擎限流写入 → `verifying`/`paused` 展示层换算（`tasks.py:44-55`）→ 契约声明"state 是展示六态"与实现相符。
- **失败≠无货铁律守住了**：403/541 → 「冷却中」；其他异常 → 「未知」；`classify` 兜底 unknown。**没有任何路径把失败写成 `unavailable`**。
- **幂等与续费**：`pay.py:98-100` order_id 去重存在；续费 `base = max(tier_expires_at, now) + 30 天`，提前续费不亏天数 ✓。
- **tier 读取实时**：引擎每 tick 直接读库，无缓存 → 后台改档位 ≤5s 生效；前台 `/quota` 实时读库。
- **审计日志真实**：`audit()` 在 `patch_user` 中确实被调用，后台可查。
- **测试不扣配额**：`test_channel` 不碰 `_check_quota`，只受每日 5 次防滥用限制。
- **购买指南**：含 Apple Store 方案/小程序直达 ✓。

---

## 五、修复优先级总排序（P0 上线前必须修）

1. 会员到期降级 + 到期展示/提醒（收入地基，断裂-1）
2. 爱发电签名对拍（上线 blocker，需用户爱发电就绪后，断裂-3）
3. 付费指引文案修正（否则持续产生坏账，断裂-14）
4. 配额护栏：间隔下限 + 先扣后发→按成功扣 + 失败自动暂停 + 预警（断裂-4/5/6）
5. 通知直达死链修复（一行代码，实测验证过，断裂-12）
6. 通知渠道前端入口：配置 + 测试 + 历史（断裂-13）
7. 任务生命周期：过期展示/续期 + 重复去重 + 体验假活 + 登录迁移 + 僵尸任务（断裂-7/8/9/10/11）
8. 支付坏账链路：金额异常队列 + 待认领视图 + 退款状态 + 补单期限（断裂-15/16/17/2）
9. 反薅：请求预算 + 门店上限 + 邮箱验证 + 通道校验（断裂-20/21/22/23）
10. 数据页：升级引导 + 榜单改名 + 高峰模式开关 + 预计送货日期（补或正式砍掉）（断裂-18/19 + 对标 1/3/4）
