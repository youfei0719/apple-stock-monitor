# 第十一轮代码审查 + 产品审查总报告

- HEAD：`b257470`（fix(round10-be): R10-I5 users真分页offset/R10-I6 effective_tier统计）
- 审查方式：三路并行（后端 / 前端+后台 / 部署+文档），全部读代码确认，关键项实测
- 结论：**不是零问题。P0 零条；P1 共 7 条；P2 共 11 条。**

---

## 一、回归验证

### 后端（6/6 通过）
1. P0-1（deploy.sh 行尾注释）：通过——后端不自解析 .env（pydantic-settings），无注释陷阱
2. P1-1（patch_user 手动降档收敛）：通过——`admin.py:339-353` TIER_RANK 新档<旧档即调 `converge_task_limit`，与 refund/sweep 同口径；降 free 先清空 tier_expires_at/pending_tier
3. batch_create N+1：通过——`tasks.py:517` 单次查出已有任务内存比对，`566` 单次 select+selectinload
4. test_round9_backend.py：通过——13 passed；全量 tests/ 127 passed
5. GET /users 真分页 offset：通过——`admin.py:193,199` offset/limit 实现
6. overview effective_tier_distribution/paid_members：通过——`admin.py:93-113` SQL case 复刻 effective_tier，有对齐测试

### 前端+后台（3/5 通过）
1. 后台真分页 GET /users offset：❌ 未通过——后端修了，前端没接（见 P1-1）
2. overview effective_tier_distribution/paid_members：❌ 未通过——后端修了，前端没接（见 P1-2）
3. 前端构建：通过——frontend/admin `npm run build` 零 error（实跑）
4. 匿名试用链路：通过——横幅/单任务创建/trialExhausted 耗尽引导全通
5. 通知渠道标注（N14-B）：通过——文案与 tiers.py channels 完全对上，测试按钮按档位禁用

### 部署+文档（7/7 通过，关键项实测）
1. P0-1 行尾注释 fail-fast：通过（实测）——污染片段 exit=1 拦截，干净不误伤；.env.example 零行尾注释；文档已写明
2. P1-D1 migrate 顺序：通过（实测）——frontend build(346) < admin build(359) < alembic(381) < restart(385)
3. systemd unit：通过——Environment= 均带等号，无静默丢弃
4. certbot 流程：通过——cp conf → certonly standalone → nginx -t/reload，deploy-hook + ERR trap 齐全
5. 前置检查：通过——git/venv/node/npm/nginx/curl/sqlite3/rsync/certbot/crontab/EUID + node≥18 全覆盖
6. backup.sh：通过——busy_timeout=15000，失败 fail()+SMTP 告警，恢复注释"先停服务"
7. 19 项功能清单：通过——README 19 项完整，预计送货日期零残留（reviews/ 历史评审除外）

---

## 二、P1（不自洽，7 条）

**P1-1** admin users offset 真分页前端未接 —— `admin/src/lib/admin-api.ts:218` getUsers 不传 offset；`members/index.tsx:129-131,232` 注释过时（"后端暂无 offset"）；无翻页 UI。修复：getUsers 加 offset 参数；members 页加分页器，按"返回条数 < limit"判末页；更新过时注释。

**P1-2** admin overview 未用 effective_tier_distribution/paid_members —— `admin-api.ts:17` OverviewKpi 类型无两字段；`overview/index.tsx:97-101` 仍用 tier_distribution 前端自算 paid，把"已到期未降档"用户计入付费 KPI（虚高）。修复：类型加两字段（可选链防御旧后端）；KPI 直接用 paid_members；删"含已到期未降档"文案。

**P1-3** trial 档邮箱输入框未禁用 —— `frontend/src/components/NotifyChannels.tsx:301-316` 邮箱对所有档位开放，但后端 `_require_channels` 对 trial 配 email 直接 400（Bark/群机器人已禁用，email 漏了）。修复：tier==='trial' 时禁用邮箱输入框，placeholder/title 注明"体验版仅支持站内通知"。

**P1-4** Link 嵌套 Link（非法 HTML） —— `frontend/src/pages/Home.tsx` TaskCard 外层 Link 包 trialExhausted 内层 Link（约 162-173 行），点击内层可能冒泡触发外层导航。修复：提示移到外层 Link 之外，或内层用 button + stopPropagation + navigate()。

**P1-5** quota.py 匿名 trial 月 key 口径错位 —— `engine.py:562` 用北京时间月份，`quota.py:32` 匿名分支用 UTC 月份；每月 1 日 00:00–08:00（北京）展示的 push_used 与引擎扣减错位，trialExhausted 横幅误判。修复：quota.py 改同口径，最好抽公共函数与引擎共用。

**P1-6** 契约文档状态枚举滞后 —— `docs/API_CONTRACT.md` 写"六态"，实现是七态（含 expired，`tasks.py:27-34` DISPLAY_STATE_ORDER，前端 StockStateBadge 已按七态实现）。修复：契约两处改七态并收录 expired 语义。

**P1-7** 行尾注释禁令名不副实 —— 运维手册称"值含 # 直接报错退出"，实现 `deploy.sh:124-145` 只拦截 APP_ENV/APP_SECRET_KEY 两个 key；其余 key（如 SMTP_PASSWORD=abc  # 注释）校验全绿但 systemd 读到污染值 → 运行时静默故障。修复二选一：(a) 扩展拦截到所有 key（引号感知，`PASSWORD='a#b'` 不误伤）；(b) 收窄文档措辞明确仅两个 key 强制拦截。

---

## 三、P2（小问题，11 条）

**后端（4）**
- P2-1：三条降级路径 quota_reset_at 口径不一——refund 和手动降档置 now+30d，membership_sweep 降 free 置 NULL（靠 ensure_quota_anchor 懒初始化）。建议 sweep 也置 now+30d。
- P2-2：patch_user 同时传 {"tier":"free","tier_expires_at":...} 造脏状态（tier 分支先清空，随后补单分支又设回去）。建议 tier 为 free/trial 时忽略或 400 拒绝 tier_expires_at。
- P2-3：engine.py:748 自动暂停文案误导——提示检查"Bark key / 邮箱 / webhook"，但全档位 400 拒绝 bark/webhook。改文案为"邮箱"。
- P2-4：死代码——schemas.py ErrorOut 零引用；auth.py _strip_channel_secrets 里 pop("sms_to") 永不命中（extra="forbid"）。

**前端+后台（2）**
- P2-5：金额 `¥19.0`——后端 amount_cny 是 Float，前端 `Me.tsx:591` + 后台 `payments/index.tsx` 5 处直接渲染。统一格式化（整数 toLocaleString，小数 toFixed(2)）。
- P2-6：USE_MOCK 死分支残留——admin-api.ts 注释称已删，实际 admin-profile.tsx:26、admin-user-footer.tsx:21、sign-in/index.tsx:158、route.tsx:31 仍有分支，sessionStorage admin-authed 无用标记。删掉。

**部署+文档（5）**
- P2-7：deploy.sh:392-393 健康检查 grep 对 JSON 空白敏感——改空白容忍 `grep -qE '"status"[[:space:]]*:[[:space:]]*"ok"'`。
- P2-8：坏 venv 不重建——deploy.sh:337 加完整性探针（`.venv/bin/python -c 'import sys' || rm -rf .venv`）。
- P2-9：restart 失败走不到诊断分支——deploy.sh:385 加 `|| { journalctl 诊断; exit 1; }`。
- P2-10：env_val 两份拷贝漂移风险——deploy.sh:35-72 vs backup.sh:62-89，抽成 deploy/lib.sh 共享。
- P2-11：契约/手册枚举滞后——运维手册订单状态漏 `unknown_plan`；API_CONTRACT payments status 枚举补 unknown_plan；overview 节同步 effective_tier_distribution/paid_members 并修正"含 trial"措辞（trial 不落库）。

---

## 四、已核验无问题（证据）
- 并发：sweep 原子 UPDATE+rowcount；claim 先原子占单；grant raw SQL 同事务；幂等键 TTL；续期双击去重
- SQLite：WAL + busy_timeout 5000 全覆盖（含 alembic env.py）
- 时区：as_naive_utc 覆盖 tasks/admin；展示 Z 后缀/北京时间统一（唯一漏网即 P1-5）
- N+1：list_tasks/_due_tasks/三 sweep/admin 全 selectinload 或聚合
- 异常：无 bare except、无 try/except pass
- 日志：无密码/token/bark_key/邮箱明文（邮箱记 sha256 前 16 位）
- Alembic：单头 d2e4f6a8b0c1，alembic check 无漂移
- 安全：无 f-string 拼 SQL；_get_owned 越权校验；admin 需 is_admin+TOTP；限流全覆盖；XFF 仅受信；验签 fail-closed
- 产品链路：匿名→注册→验证→建任务→到货→通知→配额→到期→降级→续费→退款全链路无断裂；channel 档位校验全链路一致
- 部署：migrate 顺序实测正确；unit 等号；certbot 顺序+hook；前置检查全；backup 告警链；19 项清单零残留

## 五、不计入
爱发电签名真实对拍（等用户侧就绪，唯一外部 blocker）、对账定时 job TODO、对标"可以晚点"项（微信通知、3 秒刷新档、全站城市榜单、后台运营四件套、冷启动种子数据）。

**结论：第十一轮非零问题。P0 零条；P1 共 7 条；P2 共 11 条。**
