# 第九轮代码审查 + 产品审查报告（HEAD=7591688）

**总评：不是零问题。** 断裂 6 条，不自洽 16 条，UI 问题 2 条，死代码 3 处，P3 观察 4 项。
三路回归验证（后端 / 前端+后台 / 部署+文档+产品链路）**全部通过**：第八轮修复经代码核验全部真实生效，无"注释修了代码没修"的情况。

---

## 一、回归验证（全部通过 ✅）

- systemd 两份 unit `Environment=` 等号齐全；deploy.sh env_val 解析器 11 种边界实测全过；prod 硬门槛预检与 main.py 1:1；certbot 首次部署顺序正确；前置检查 10 项 + EUID + 60s 心跳轮询；backup.sh busy_timeout + SMTP 告警链 + 恢复注释
- `_as_naive_utc` 已提升到 `core/timeutil.py`，`patch_user` 入口归一化；后-D-2（用户选①）`_require_channels` 按档位逐个校验，bark/webhook → 400 `channel_not_supported`，空渠道 400 只推荐邮箱，`/notify/test` 文案诚实；并发/时区/幂等写法正确；Alembic 单头；pytest 100 passed；ruff 通过
- App 层 logout 清 state；渠道文案与 tiers.py 真源一致；前后端契约抽查全对齐；后台退款/关闭/认领/手动验邮入口与后端行为一致
- 产品链路走查：匿名试用→注册→验证→登录认领→建任务→到货→通知→配额→到期→降级→续费→退款，全链路可走通

---

## 二、断裂（6 条，必须修）

### 后端（2）

**R9-D1 · 门店目录刷新"全量替换"丢数据** — `backend/app/api/routers/catalog.py:128-168`（`_do_refresh_stores`）
- 依据：`merged` 从空 dict 起步，只收录本轮成功 anchor 发现的门店；任一 anchor 抛 `AppleError` 时只是 `errors+=1` 继续，但最后 `if merged:` 就全量覆盖 `STORE_CATALOG_KEY`。注释写明"部分 anchor 失败"是预期场景，写路径却按"全成功"处理——一次部分失败的刷新会把失败 anchor 的门店（含种子店）从目录里删掉。
- 修复：先读出现有目录做底再合并，或仅当 `errors == 0` 时才全量替换。

**R9-D2 · 认领订单并发双击 → 重复开档（多送 30 天会员）** — `backend/app/api/routers/admin.py` `claim_payment`（约 377-437 行）
- 依据：`if p.user_id is not None: raise` 是 check-then-set；两个并发请求可同时通过检查，各自执行 `apply_tier_grant`（原子 UPDATE 是"每次调用都 +30 天"，防 lost-update 而非防重复调用）。SQLite 下两次事务都能提交 → 用户多得 30 天付费会员（资损方向）。
- 修复：`UPDATE payments SET user_id=:u WHERE id=:id AND user_id IS NULL` + rowcount 校验，rowcount=0 按 `already_claimed` 处理。

### 前端+后台（3）

**R9-D3 · admin 侧边栏退出不清路由守卫缓存**
- 依据：`admin/src/components/layout/admin-user-footer.tsx:12-30` 的 `signOut()` 只删 sessionStorage + 调 `/api/auth/logout`，没有调用 `invalidateAdminGuardCache()`（`routes/_authenticated/route.tsx:24-26` 的 `guardPassed` 模块缓存只在 `admin-profile.tsx:25` 的退出路径清除）。
- 后果：从侧边栏退出后守卫仍放行，各接口 401 → 页面卡"加载失败"而不是跳登录页。两条退出路径行为不一致。
- 修复：`signOut` 内同样调 `invalidateAdminGuardCache()` 并补二次确认；删掉该文件 20 行的过期 TODO。

**R9-D4 · AddMonitor 登录 batch 链路：PATCH 补齐失败被渲染成"创建失败"，实际任务已创建**
- 依据：`frontend/src/pages/AddMonitor.tsx:224`，`Promise.all(created.map(t => api.updateTask(...)))` 在外层 try 内，任一 PATCH 失败 → 外层 catch 显示"创建失败…"，但此时 `batchTasks` 已成功（任务已入库），失败的只是 group/repeat_interval_sec 补齐。用户点重试会撞后端 409 查重。
- 修复：PATCH 阶段单独 catch，文案改为"任务已创建，但分组/重复间隔设置失败：xxx"，并照常 `refreshTasks + navigate('/')`。

**R9-D5 · admin 总览"活跃任务"KPI 口径与全站"监控中"口径打架**
- 依据：后端 `admin.py` overview：`active_tasks = count(MonitorTask where paused IS False)`——含已过期未暂停、不含已暂停；而全站"监控中"口径（`/tasks?status=active`、Home activeCount、灵动岛）是 `paused || !expired`。
- 修复：admin overview 改成 `paused IS True OR expires_at IS NULL OR expires_at > now`，与 `/tasks?status=active` 对齐。

### 部署（1）

**R9-D6 · certbot 自动续期会失败（~90 天潜伏期），HTTPS 将中断** — `deploy/deploy.sh:202-208`
- 依据：`certbot certonly … --standalone … --deploy-hook "systemctl reload nginx"`——只有 `--deploy-hook`，无 `--pre-hook/--post-hook`。renewal 配置记录 `authenticator = standalone`；certbot timer 续期时 standalone 必须独占 :80，但 nginx 常驻监听 :80 → bind 失败 → 续期失败 → 证书到期后 HTTPS 中断。`--deploy-hook` 只在续期成功后触发，救不了失败的续期。运维手册"后续续期走 certbot 自动续期，无需人工"与事实不符。
- 修复：certonly 追加 `--pre-hook "systemctl stop nginx" --post-hook "systemctl start nginx"`；或改 webroot 方式 + 80 server 块加 `location /.well-known/acme-challenge/` 直通例外。

---

## 三、不自洽（16 条）

### 后端（3）

**R9-I1 · `task_expiry_sweep` 删 trial 任务留下悬空 `notification.task_id`** — `backend/app/services/lifecycle.py`（约 340-360 行）
- 依据：`db.delete(t)` 直接删任务；`delete_task` 接口（tasks.py:610）有显式 `update(Notification)…values(task_id=None)`（因 db.py 未设 `PRAGMA foreign_keys=ON`，SQLite 层 ondelete='SET NULL' 不触发）。sweep 的 7 天过期 trial 自动删除是同一漏洞的漏网之鱼。
- 修复：sweep 删除前同样先置 NULL（或把"删前置空"抽成公共函数两处复用）。

**R9-I2 · `unknown_plan` 订单状态守卫与档位守卫矛盾** — `backend/app/api/routers/admin.py` `claim_payment`
- 依据：状态守卫允许 `("paid","amount_mismatch","unknown_plan")`，但紧接着 `tier_to not in ("standard","pro") → 400`；webhook 落库时 unknown_plan 的 `tier_to` 恒为 `""`，该分支永远 400。守卫宣称 unknown_plan 可认领，实际永远走不通（正确路径是 close）。
- 修复：状态守卫改为 `("paid","amount_mismatch")`，或提供 unknown_plan 补 plan 映射入口。

**R9-I3 · webhook 用户关联未做邮箱归一化** — `backend/app/api/routers/pay.py` `_resolve_user`（约 51-57 行）
- 依据：`User.email == remark` 直接比对原始 remark；register/login/resend 全链路已统一 `strip().lower()`。买家备注填 "Foo@X.com "（大小写/空格差异）时匹配失败 → 订单落为"未认领"靠人工认领。
- 修复：`remark.strip().lower()` 后再查。

### 前端+后台（9）

**R9-I4** — `TaskDetail.tsx:156` 续期消息仍用 `renewMsg.includes('已续期')` 文本匹配判颜色，同文件 `saveChannels` 已改用布尔。修复：加 `renewOk` 布尔状态对齐。

**R9-I5** — admin 登录冷却文案误导：`sign-in/index.tsx:180-182` 前端 60 秒冷却文案"当前 IP 将被锁定。请 60 秒后再试"，后端实际是 5 次失败锁 15 分钟（`config.py:58-59`），60 秒后重试必吃 429 陷入死循环；且 TOTP 失败（按 user 计锁）也会触发"IP 将被锁定"文案。修复：冷却时长读后端 429 剩余秒数，或文案改"IP 已被锁定约 15 分钟"；TOTP 失败单独文案。

**R9-I6** — admin 401 无统一跳转：`main.tsx:54` 的 react-query 401 拦截是死逻辑（全站数据走 `admin-api.ts` 原生 fetch，无 useQuery 在用）。会话页内过期只显示"加载失败"不引导重登。修复：在 `admin-api.ts` 的 `req()` 内统一处理 401 跳 `/sign-in`，或删掉死逻辑。

**R9-I7** — 时区口径混用：有 `timeZone:'Asia/Shanghai'`（NotifyChannels:139,163、Me:75） vs 无 timeZone 走设备本地（Me:164/569、History:19、Home:10,15、TaskDetail:96,236）；前台页脚承诺"页面内所有时间为本地时间"与显式北京时间写法冲突。修复：统一二选一（建议全站显式 Asia/Shanghai 并改页脚文案）。

**R9-I8** — `admin-api.ts` 两处注释打架：`AdminUser` 注释称后端"已返回 email_verified"，`verifyUserEmail` 注释称"暂未返回该字段"（实测已返回，后者是过期注释）。

**R9-I9** — Me 页配额加载失败静默消失：`Me.tsx:336-340` `api.quota().catch(() => setQuota(null))` 后 `{quota && (...)}`，失败连错误态都没有。

**R9-I10** — 改密未统一 API 客户端：`Me.tsx:216-229` 组件内直调 fetch，`api.ts` 无 `changePassword`。修复：`api.ts` 加 `changePassword` 走统一 `req()`。

**R9-I11** — NotifyChannels 档位文案首帧闪烁：`tier` 初始 null 走非 trial 分支，匿名用户 effect 后才 setTier('trial')。修复：`tier===null` 时不渲染文案。

**R9-I12** — 改级补单后端 notices 被前端丢弃：`updateUserTier` 返回 `Promise<void>`，后端 PATCH 默认 +30 天并返回 `notices`，前端 toast 用自己传的 iso 渲染，与实际生效值不一致。修复：返回响应体并展示 notices。

### 部署/文档（4）

**R9-I13** — `docs/运维手册.md:61` 行号引用错误（R8 宣称已修复，实际回归失败）：手册写"第 154 行"，实际 `deploy.sh:166`；根因是修复提交自己加了 +10 行把行号顶走。修复：去掉行号引用（硬编码行号已两次漂移）。

**R9-I14** — 续费/升级不自动恢复降级时被自动暂停的任务：降级时 `lifecycle.py:189/70` 把超限任务置 `paused=True, paused_reason="tier_limit"`，但 webhook/admin 改档/claim 三条升级路径全都不恢复；唯一自动恢复的是 `quota_exhausted`。用户续费回 pro 后 27 个任务继续暂停且占名额，需逐个手动恢复，前端无提示。链路不对称。

**R9-I15** — 注册链路文案不诚实：`auth.py:129-152` 验证码首发失败只记日志不返回（`UserOut` 无 email_sent 字段），而 `VerifyEmail.tsx:71` 断言"6 位验证码已发送到你的邮箱"。SMTP 瞬断时用户在验证页干等一封从未发出的邮件。修复：register 响应带 `email_sent`，前端据此展示"发送失败，请点重新发送"。

**R9-I16** — `docs/API_CONTRACT.md:104` 过度承诺：写 resend-code"永远返回 200"，但 `auth.py:336-338` SMTP 发送失败返回 500 `email_failed`。修复：改契约为"未知邮箱仍 200 防枚举；发送失败 500"。

---

## 四、UI 问题（2）

**R9-U1** — 退款 force 二次确认用原生 `window.confirm`（`admin/src/features/payments/index.tsx:161`），与 R8-U-8"应用内弹窗替代原生 confirm"规范不一致。修复：复用页内 AlertDialog。

**R9-U2** — 流量页补零日期键用浏览器本地时区（`traffic/index.tsx:27-40`），后端 day 键是北京时间，非北京时区管理员日期错位（北京用户无影响，低严重）。

---

## 五、死代码（3）

- `admin/src/lib/admin-api.ts:495` `getLogTail` 死导出（system 页已明确不再调用）
- `admin/src/lib/admin-api.ts` `USE_MOCK=false` 恒定 → 所有 mock 分支 + MOCK_USERS/MOCK_PAYMENTS/MOCK_AUDIT/MOCK_LOGS/mockTraffic（约 150 行）不可达
- `admin/src/components/layout/admin-user-footer.tsx:20` 过期 TODO（"TODO(后端联调): 真实登出"——实际已调 logout）

---

## 六、P3 观察项（4，供斟酌，影响小）

- O1：任务创建查重/上限 check-then-insert 竞态（无唯一约束；幂等键覆盖重试路径、前端已防抖，残留窗口小）
- O2：`batch_create` 无独立 IP 限流（只有全局 60/min 兜底；影响被档位 tasks_limit 封顶）
- O3：登录密码预言——密码正确但邮箱未验证 → 403 `email_unverified`，密码错误 → 401，可区分"密码正确"（未验证账号）
- O4：`sessions` 表无清理（过期 session 永不删除；lifecycle 有 prune_api_hits/prune_notifications，缺 prune_sessions）

---

## 七、已知遗留（不计入）

爱发电 webhook 签名真实对拍（等用户侧就绪，上线前唯一外部 blocker）、对账定时 job TODO、对标"可以晚点"项（微信通知、3 秒刷新档、全站城市榜单、后台运营四件套、冷启动种子数据）。

**结论：第九轮非零问题。** 断裂 6 + 不自洽 16 + UI 2 + 死代码 3 + P3 观察 4。
