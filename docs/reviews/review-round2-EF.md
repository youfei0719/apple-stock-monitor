# 第二轮产品逻辑审查 · E/F 维度（数据与冷启动 / 支付闭环）

## E. 数据与冷启动

### 🔴 断裂

**E-1「全国榜单」名不副实：实际是"我的放货城市分布"**
- 依据：`backend/app/api/routers/analytics.py:18-30`（`_event_rows` 固定过滤 `Notification.user_id == user.id`），`ranking()`（47-57 行）按该用户自己的有货通知聚合城市；前端 `frontend/src/pages/History.tsx:25` tab 名叫"全国榜单"，标题"城市放货排行 · 今日"。
- 场景复现：用户只监控深圳 3 家店，榜单永远只有"深圳"一行；两个都只盯深圳的用户看到的"全国榜单"完全不同。DING果的是真·多用户聚合的全国城市榜。
- 成熟做法：要么做真全国聚合（全站 `stock_alert` 按城市×机型匿名聚合），要么改名"我的放货分布"。
- 修复建议：短期改名 + 空状态文案诚实化（"数据随全站用户积累"）；长期等用户量起来做系统级聚合。**优先级：必须补**（名称即承诺，名不副实是信任问题）。

**E-2「放货记录」tab 对免费用户是 403 报错页，不是升级引导**
- 依据：`backend/app/api/routers/history.py:66-69`（`tier_of(user.tier)["history"]` 为 False 直接抛 403）；`History.tsx:56` 的 `load()` 对 releases 只做 `catch → ErrorState`，无档位判断、无升级入口。
- 场景复现：免费用户点"放货记录"tab → 看到红色错误"完整历史数据需要标准版及以上" → 没有"去开通"按钮，卡住。
- 成熟做法：DING果是整页升级引导 + 一键开通。
- 修复建议：前端捕获 `code === "tier_required"` 时渲染升级卡片（跳转 Me 页付费区）。**优先级：必须补**（付费转化漏斗断了）。

### 🟡 不自洽

**E-3"完整历史"的档位语义含糊：四个 tab 只有"放货记录"被门控**
- 依据：门控只在 `/history/releases`（history.py:66-69）；`/history/events`（活动日志，30 天）无门控，`/analytics/ranking`、`/analytics/overview`、`stats/poll` 全部无门控。`tiers.py:4-37` 只定义了 `history: True/False`，没定义"完整"指什么。
- 场景：免费用户能看 30 天活动日志 + 榜单 + 数据分析，却被告知"完整历史需要标准版"——用户困惑。
- 修复建议：二选一并写进 `docs/API_CONTRACT.md` + 前端标注：(a) 免费版活动日志限 7 天、放货记录/榜单/导出要会员；(b) 保持现状但把"放货记录"tab 直接标"会员"角标。**优先级：可以晚点**。

**E-4 降级后历史数据是"隐藏"（实现正确），但降级路径本身不可达**
- 依据：全库无删除 notifications 的逻辑（grep 确认），门控只是 403 隐藏，续费后恢复可见——这是成熟做法 ✓。但 `tier_of()` 不校验 `tier_expires_at`，且到期降级根本不存在（见 F-1），所以这条是死代码路径。
- 修复建议：和 F-1 一起修，用统一的 `effective_tier()` 包一层过期判断。

### ⚪ 对标差距

**E-5 冷启动第一天：四个 tab 全空，无任何种子/系统级数据**
- 依据：唯一数据源是 `notifications` 表（`kind=stock_alert & status=sent`）。空状态设计是有的（History.tsx:73-74/96-97/113-114/135 都有 EmptyState）✓，但 DING果第一天就有真实历史可看。
- 我们拿什么填：目前什么都没有。`SystemConfig` docstring（models.py:187-190）提到"种子数据"，但实际只 seed 了门店目录（catalog.py:100-201）。
- 修复建议：上线前用官方账号跑一批真实监控任务攒种子，或诚实标注"数据积累中 · 已收录 N 条"。**优先级：可以晚点**。

**E-6 无保留/清理/归档策略**
- 依据：notifications 只增不减；history API 允许查 365 天（history.py:30）；运维手册只有整库备份保留 14 天，无数据归档。
- **优先级：可以晚点**（量小，SQLite 撑得住；上线前定策略写进运维手册即可）。
- 已有项：购买指南含 Apple Store 方案/小程序直达（guide.py:13-16）✓、通知链路测试（API_CONTRACT.md:40）✓——对标 DING果这两项不缺。

## F. 支付闭环

### 🔴 断裂

**F-1 会员到期不降级：付一次，永久会员**
- 依据：webhook 只写 `tier` + `tier_expires_at`（pay.py:120-127）；全代码库没有任何地方读 `tier_expires_at` 做降级——`deps.py:get_current_user` 不校验（只校验 session 过期，deps.py:44），无 cron/定时任务，`tier_of()` 不看过期时间。
- 场景复现：用户 10-09 付 ¥19 开标准版（`tier_expires_at`=11-08），11-09 之后依然是标准版：10 个任务、100 次推送、完整历史全保留，永远不需要续费。**整个订阅商业模式地基是空的**。
- 成熟做法：DING果/标准 SaaS = 到期自动降级 + 到期前提醒。
- 修复建议：加 `effective_tier(user)`（过期 → 按 free 算），所有 `tier_of(user.tier)` 调用点替换；或在 `get_current_user` 做惰性降级写回。**优先级：必须补**。

**F-2 取消续费 / 退款没有任何承接**
- 依据：webhook 不解析任何事件类型字段（pay.py 全函数无 type/event 判断）；`Payment.status` 只有 `'paid'` 一种值（models.py:166 default，admin 前端注释也确认：admin/src/features/payments/index.tsx:34-36）。
- 场景复现：(a) 用户在爱发电取消自动续费 → 本站无感知，叠加 F-1，到期后继续白嫖；(b) 用户在爱发电申请退款成功 → 本站订单仍是 paid、会员照用——钱退了，服务照享。
- 修复建议：短期 = 退款走人工（admin 订单标状态 + 手动降级，需要先给 Payment 加状态字段）；长期 = 对账任务。**优先级：必须补**。

**F-3 验签算法未经官方对拍，注释明写"联调前勿用"**
- 依据：pay.py:1-16 docstring——"该算法……。上线前必须用真实回调做一次签名对拍"。
- 场景：上线后真实回调验签失败 → 全部 403 → 用户付钱、一个都开不了通。
- 修复建议：上线前用爱发电后台真实回调对拍签名，这是上线 blocker。**优先级：必须补**。

**F-4 金额强校验 + 失败无通知 = 钱货两空黑洞**
- 依据：pay.py:109-119，`amount_fen` 必须精确等于 1900/3900，否则 400 拒绝；失败只有 `log.error`，无管理员通知、无用户通知、无待处理队列。
- 场景复现：用户用优惠券/自定义金额支付、或档位调价后配置滞后 → 爱发电已扣款（不退），本站 400 拒绝 → 用户无会员，管理员不知情，只能等用户投诉。
- 成熟做法：金额不符 → 订单进"异常待处理" + 通知管理员人工核验，而不是直接丢弃。
- 修复建议：`amount_mismatch` 时仍落库（status='amount_mismatch'，user_id 尽量关联），admin 待处理视图 + Bark/邮件告警。**优先级：必须补**。

**F-5 前端付费指引写错了：让用户"使订单号与邮箱保持一致"**
- 依据：Me.tsx:170-172 "爱发电订单号请与账号邮箱保持一致，以便自动开通"。事实：`out_trade_no` 由爱发电生成，用户改不了；后端实际认的是 **remark**（pay.py:86-97：纯数字→user_id，含 @→email）。
- 场景复现：用户照着做（做不到），remark 留空 → webhook 找不到用户 → `Payment.user_id=None` → 付了钱没开通，直接掉进 F-7 的坏账。
- 修复建议：文案改为"赞助时在备注/留言中填写你的用户 ID #123 或注册邮箱"（Me.tsx:102 已显示 `#id`，直接引用）。另：开通按钮跳的是 `https://afdian.com` 首页（Me.tsx:167），应配成自家赞助页 URL（`AFDIAN_PAGE_URL`）。**优先级：必须补**（文案错误是 F-7 的上游）。

**F-6 webhook 丢失无对账/补单机制；人工补单不设到期时间**
- 依据：无定时对账；运维手册 docs/运维手册.md:28 写"失败订单人工补单（改级）"，实际是去 members 页手动改级（admin/src/features/members/index.tsx:194-204）；`AdminUserPatchIn` 无 `tier_expires_at` 字段（schemas.py:110-113），`patch_user` 只改 tier（admin.py:123-151）。
- 场景复现：webhook 丢了 → 用户投诉 → admin 手动改 standard → 该用户**永久** standard（无到期），与正常付费用户（30 天到期）不一致；且 Payment 记录的 user_id 仍是 None，账实分离。
- 修复建议：`AdminUserPatchIn` 加 `tier_expires_at` 可填项；补单时联动回填 Payment.user_id；长期加对账 job（拉爱发电订单 API 比对）。**优先级：必须补**（至少先修补单入口）。

**F-7 未认领订单在后台不可识别归属 → 无法补单**
- 依据：`admin.py:152-186 list_payments` 不返回 `raw_payload`（remark 在里面）也不返回 email；API_CONTRACT.md:66 明确"无 email 字段"。
- 场景复现：用户 remark 填错 → 后台看到一笔 `user_id=—` 的 ¥19 → 不知道是谁的钱 → 无法补单，只能让用户报订单号、再进库查 raw_payload。
- 成熟做法："待认领订单"视图（展示 remark/时间/金额）+ 一键绑定用户。
- 修复建议：admin payments 接口返回 remark（从 raw_payload 提取）+ 列表页加"待认领"筛选 + 绑定用户接口。**优先级：必须补**。

**F-8 用户看不到会员到期时间，无到期提醒**
- 依据：后端 `/quota` 返回了 `tier_expires_at`（quota.py:31），但前端 `Quota` 接口根本没定义该字段（lib/api.ts:179-187），Me.tsx 不展示；无到期前提醒逻辑。
- 场景：用户不知道何时到期 → F-1 修好降级后，到期当天突然被降级 → "我明明充过钱"投诉。
- DING果：会员页明确到期时间 + 到期提醒。**优先级：必须补**（和 F-1 一起修，前端加到期展示 + 到期前 3 天推送）。

### 🟡 不自洽

**F-9 手动改级 vs webhook 开通：到期时间语义不一致**
- 依据：webhook 开通设 `+30 天`（pay.py:126）；admin 手动改级不碰 `tier_expires_at`（admin.py:123-151）。同一 "standard" 会员，一个有到期时间一个没有。
- 修复建议：F-6 修了之后自然一致。

**F-10 "体验"档在会员页展示但注册用户拿不到**
- 依据：`GET /plans` 返回全部 4 档含 trial（quota.py:34-52，price 0）；前端会员档位区展示"体验 免费"。实际注册默认 `free`（models.py:31），trial 只用于未登录匿名设备任务（engine.py:162，tasks.py:99-100"请登录后使用"）。
- 用户看到"体验 ¥0"以为可开，实际点不进来。轻微，修法：plans 接口不返回 trial，或标注"未登录体验"。

### 🟢 已验证通过

**F-11 幂等**：`pay.py:98-100` order_id 去重存在。极小竞态：并发双回调同时通过 dup 检查 → 唯一约束抛 IntegrityError → 500（pay.py 无 except IntegrityError）。但同一事务回滚、不会重复开通；爱发电重试后走 dup 分支自愈。钱安全，可接受；建议加 try/except 转 200-dup 更干净（可选）。

**F-12 续费延长逻辑正确**：pay.py:124-126，`base = max(tier_expires_at, now) + 30 天`，提前续费不亏天数 ✓。用户端能看到订单记录（Me.tsx:150-178：plan/金额/日期/订单号 ✓）和当前档位（Me.tsx:90-96 ✓）。

## 必须补清单（按依赖排序）
1. **F-3** 爱发电签名联调对拍（上线 blocker）
2. **F-5** 付费指引文案修正 + 赞助页直链（否则持续产生坏账）
3. **F-1** 到期降级（`effective_tier`）+ **F-8** 到期展示与提醒（一起做）
4. **F-7** 待认领订单视图 + remark 暴露 + 绑定用户
5. **F-6** 补单入口补 `tier_expires_at` + 对账 job（长期）
6. **F-4** 金额异常进待处理队列 + 管理员告警
7. **F-2** 退款/取消的人工链路（Payment.status 加 refunded/cancelled）
8. **E-1** 全国榜单改名或做真聚合 + **E-2** 放货记录 tab 的升级引导
