# 第十轮代码审查 + 产品审查报告（HEAD=a0955d8）

- 审查方式：三路并行（后端 / 前端+后台 / 部署+文档），只读审查，全部读代码确认
- 结论：**非零问题**。P0 × 1，P1 × 12，P2 × 26

---

## 一、回归验证（第九轮修复逐条核验）

### 后端（7/7 通过）

| 项 | 结论 | 证据 |
|---|---|---|
| R9-D1 门店刷新合并+全失败保留旧目录 | ✅ | `catalog.py:109-163`：`merged` 先装现有目录（124-127），成功 anchor 覆盖（130-139）；`148` 仅 `refreshed_any and merged` 才写库+推进时间；`156` 全失败只打日志不写库；限流路径（143-144）同样不覆盖旧目录 |
| R9-D2 认领原子 UPDATE+rowcount，占位在 grant 前 | ✅ | `admin.py:431-440`：`update(Payment).where(id, user_id.is_(None))` + `rowcount != 1 → rollback + already_claimed`；`apply_tier_grant` 在 444 行，顺序正确 |
| R9-D5 overview 口径与 tasks 谓词对齐 | ✅ | `admin.py:105-114` 谓词 `paused ∨ expires_at IS NULL ∨ expires_at >= now`，与 `tasks.py:62-68` `_is_expired` 的德摩根形式逐字一致；手动暂停任务两边都算 active |
| R9-I14 三条路径恢复 tier_limit 暂停 | ✅ | `pay.py:303`（webhook granted）、`admin.py:449`（admin claim）、`admin.py:302`（admin 改档），三处档位提升/续费成功后调 `resume_tier_limited_tasks`，只恢复 `paused_reason="tier_limit"` 且按新档位限 slots |
| O2 batch 独立 IP 限流 | ✅ | `tasks.py:449` 键 `batch_create:{ip}` 10次/小时，与 `task_create:{ip}`（372行）独立另计 |
| O3 未验证账号不泄露密码正确性 | ✅ | `auth.py:348-363`：不存在→401；未验证→403（不论密码对错、不记登录失败）；已验证+密码错→401，401/403 不可区分 |
| O4 sessions 清理 | ✅ | 改密 `auth.py:462-468` 删其他 session；退出 `auth.py:392-403` 删当前 session；`lifecycle.py:582 prune_sessions` 每天清过期并入 sweep |

### 前端+后台（4/5 通过，1 半通过）

| 项 | 结论 | 证据 |
|---|---|---|
| 后-D-2 Bark/ webhook 档位校验 | ✅ | `tasks.py:211-241` `_require_channels` 在 `POST /tasks`（:393）、`POST /tasks/batch`（:490）、`PATCH /tasks/{id}`（:554）三处调用；配 bark/webhook → 400 `channel_not_supported`，文案诚实 |
| N14-B 落实 | ✅ | `NotifyChannels.tsx:186-191` channelNote；`notify.py:57-70` test 先档位校验再限额。但文案有新问题见 R10-I3 |
| 401 统一跳登录 | ⚠️ 半通过 | admin 侧 ✅（`admin-api.ts:135-145` 统一拦截 401→清守卫缓存→跳 sign-in）；C 端 ❌（`api.ts` req() 无 401 拦截，见 R10-I2） |
| 时区 | ✅ | Home/TaskDetail/History/Me/NotifyChannels 全显式 `timeZone:'Asia/Shanghai'`；页脚"页面内所有时间为北京时间（UTC+8）"；admin fmtLocalTime 统一北京 |
| 契约对齐 | ✅ | 抽查 8 组接口（tasks/quota/history/notifications/pollStats/plans/payments/channelHealth）字段全对齐；admin 侧 overview/users/payments/audit/system/peak-mode 全对齐 |

### 部署+文档（4/4 通过）

| 项 | 结论 | 证据 |
|---|---|---|
| R9-D6 certbot hooks | ✅ | `deploy.sh:224-227`：`--pre-hook "systemctl stop nginx" --post-hook "systemctl start nginx" --deploy-hook "systemctl reload-or-restart nginx"`；ERR trap 经 bash 5.2 实测：certbot 失败→trap 起 nginx→退出 1 |
| unit Environment 等号 | ✅ | `stockmon-api.service:13` / `stockmon-engine.service:11` 等号都在；STOCKMON_LOG_NAME 双进程分离 |
| .env.example ↔ config.py | ✅ | 44/44 对齐；SSL_EMAIL/BACKUP_ALERT_TO 标注为运维层键 |
| backup.sh | ✅ | fail()→alert() SMTP 告警链；`PRAGMA busy_timeout=15000`（:101）；sqlite3 本体有 `\|\| fail(...)` |

**回归总评：第九轮修复全部真实生效，无"注释修了代码没修"。**

---

## 二、P0 断裂 × 1

### P0-1 · systemd 不剥行尾注释 × 模板自带行尾注释 × 文档明示"可以写" → 生产配置静默污染
- 文件：`.env.example:15,16`（行尾注释）、`.env.example:12`（文档写"行尾注释可以写"）、`deploy/stockmon-api.service:9` / `stockmon-engine.service:9`（EnvironmentFile）
- 代码依据：systemd v255 `src/basic/env-file.c` 的 `parse_env_file_internal` VALUE 状态无任何 COMMENT 处理，行尾 `#` 原样进入值。
- 爆炸路径：运维手册让 `cp .env.example .env` 后填值，模板 15/16 行自带 `APP_ENV=dev  # dev | prod`，操作员大概率保留注释。deploy.sh 的 `env_val` 会剥注释→校验绿灯；bash `source` 也剥→migrate 正常；但 systemd 读到 `APP_ENV="prod  # dev | prod"` → `is_prod` 为 False → **生产跑 dev 语义**：lifespan prod 硬门槛永久失效（`main.py:47`），`secure=settings.is_prod`（`auth.py:119`）→ 生产 HTTPS cookie 丢 Secure 标志。
- 修复：① deploy.sh 加 R8-I-1 同款 fail-fast 拦截行尾注释（至少对 APP_ENV/APP_SECRET_KEY 精确值校验）；② `.env.example` 把 15/16 行注释移到独立行，第 12 行改为"**禁止**行尾注释（systemd EnvironmentFile 不剥，会污染值）"；③ 运维手册同步。

---

## 三、P1 不自洽 × 12

### 后端 × 3

**P1-1 · `admin.py:213-246` patch_user 手动降档不做任务数收敛（真实业务漏洞）**
- `refund_payment`（597行）降档后立即 `converge_task_limit(reason="tier_limit")`，但 patch_user 降档（pro→standard / pro→free）无任何收敛。
- 后果：admin 把未到期 pro 手动降为 standard，超限任务继续轮询、继续扣配额；降为 free 时 `tier_expires_at` 被清空（242行），`membership_sweep` 只扫 standard/pro，该用户**永远**逃过自动收敛。
- 修复：降档分支后调用 `converge_task_limit(db, target, reason="tier_limit")`，暂停数写入 notices/changes。

**P1-2 · `tasks.py` batch_create 三处 N+1（性能债）**
- ① 480行循环内每 combo 调 `_find_conflict`（137行），内部全表拉回 Python 逐条比对，最多 900 次查询；② 522行 `[_task_out(t, db) for t in created]` 逐任务懒加载 states；③ `_idempotent_replay`（327行）同样逐条 `_task_out`。
- 修复：冲突检测单次查询后内存比对；返回前 `selectinload` 预加载 states。

**P1-3 · 第九轮后端修复零测试覆盖（循环可持续性问题）**
- `tests/` 只有到 `test_round8_backend.py`；D2 原子认领、I14 三条恢复、D5 口径、O2/O3/O4 均无回归测试。
- 修复：补 `test_round9_backend.py`。

### 前端+后台 × 6

**R10-I1 · admin 认领订单的后端 notices 被前端丢弃**
- `admin-api.ts:288-292` `claimPayment(): Promise<void>`；`payments/index.tsx:134-136` 只 toast 固定文案。
- 后端 `admin.py:470-481` 返回 `{notices: [...], tier_expires_at, tier, user_id}`（如"自动恢复 N 个因档位超限被暂停的监控任务"），用户永远看不到。
- 修复：`claimPayment` 返回响应体（仿 `UpdateUserTierOut`），toast 拼接 notices。

**R10-I2 · C 端会话页内过期 401 与"匿名体验"混淆**
- `App.tsx:99-107`：`/me` 401 → 一律 `setMe(null)` 走匿名横幅。
- 已登录但会话过期（如别处改密被登出）的用户看到"匿名体验中"横幅，误以为账号被降级；页内其他接口 401 只显示"请求失败（401）"无重新登录引导。
- 修复：App 层区分"曾经有会话"→渲染"登录已过期，请重新登录"横幅/跳转。

**R10-I3 · 渠道文案"可配置"不诚实（后-D-2 配套）**
- `NotifyChannels.tsx:186-191` channelNote："Bark / 群机器人可配置但到货不会发送（测试通过 ≠ 到货会发）"；`:250` Bark 输入框 placeholder 同式样。
- 后端 `_require_channels`（`tasks.py:228-238`）对**所有档位**配 bark/webhook 直接 400；`tiers.py` 无任何档位含 bark。"可配置"是假的；"测试通过"也不成立（bark 测试任何档位都 400）。
- 修复：文案改为"当前档位不支持，填写后提交会被拒绝"，或输入框直接禁用并注明"暂未开放"。（用户已拍板后-D-2 选①：按档位校验直接 400）

**R10-I4 · TaskDetail"保存通知渠道"未过滤空 webhook 行 → 422**
- `TaskDetail.tsx:66-84` `saveChannels` 直接 PATCH 原始 channels；`NotifyChannels` 允许添加 url 为空的 webhook 行；后端 `WebhookIn.url` `min_length=1`（`schemas.py:67`）→ 422 英文直出。
- `AddMonitor.tsx:184-190` `cleanChannels` 已做过滤，两处不一致。
- 修复：抽成 `lib` 函数两处共用。

**R10-I5 · 后台会员列表无分页，超 50 人静默截断**
- `members/index.tsx` 无分页 UI；`admin-api.ts:218` `getUsers()` 不传 limit；后端默认 50（`admin.py:161`，最大 200）。
- 修复：加分页（后端加 offset 参数）或传 `limit=200` + 截断提示。

**R10-I6 · 总览"付费会员"KPI 把"过期未 sweep"用户也算成付费会员**
- `overview/index.tsx:143-150` 按 `tier_distribution`（DB 原始值）统计；到期后要等 sweep 才降为 free，窗口期内已过期用户仍被计入。
- 与 `effective_tier` 全库口径矛盾。修复：后端按有效档位统计，或前端标注"含到期待降级"。

### 部署/文档 × 3

**P1-D1 · migrate 跑在前端构建之前 → "新库+旧代码"中间态**
- `deploy.sh:281`（alembic）vs `:287`/`:294`（npm build）vs `:303`（restart）。
- npm 构建失败（`npm ci` 网络抖动常见）→ set -e 退出 → DB 已是新 schema、服务仍跑旧代码；migration 非向后兼容时旧服务崩溃循环。
- 修复：migrate 移到两次 `npm run build` 成功之后、`systemctl restart` 之前。

**P1-D2 · WAL 恢复流程没处理 -wal/-shm 残留**
- 运维手册恢复步骤只有 `sqlite3 ".restore"`（只写主库）；服务崩溃后残留 -wal 与 restore 进去的主库帧不匹配 → 下次打开损坏风险。
- 修复：stop 后先 `PRAGMA wal_checkpoint(TRUNCATE)` 并确认无 -wal/-shm，再 `.restore`。

**P1-D3 · README 本地开发 .env 路径与运维手册矛盾**
- README 写仓库根 `cp .env.example .env` 再 `cd backend` 跑 alembic；但 `config.py:12` 按进程 CWD 读 `backend/.env`，根目录那份不会被读到。运维手册已修正。
- 修复：README 同步为 `cd backend; cp ../.env.example .env`。

---

## 四、P2 小问题 × 26

### 后端 × 7
1. `pay.py:194`：`amount_fen = int(float(...))` 在 try 之外；`total_amount` 非数字 → ValueError → 500 → 爱发电无限重试黑洞。修复：try 包裹，解析失败按 `amount_mismatch` 落库待人工。
2. `engine.py:553-557`：`_trial_quota_state` 匿名配额按 UTC 月界（`strftime("%Y-%m")`），与全库北京时间口径不一致（每月 1 日 0:00–8:00 配额错月）。
3. `lifecycle.py:560`：`_pruned_today` 用 UTC 日期做"每天一次"去重，与 `_today_start` 北京时间口径不一致（仅影响清理节流）。
4. O3 计时侧信道：`auth.py:357` 未验证分支直接 403 跳过 `verify_password`（bcrypt 耗时），计时可区分密码对错。网络抖动下难利用，记一笔。
5. `engine.py:140`：`except ValueError: pass` 心跳解析失败静默（fail-safe 方向可接受，建议补 debug 日志）。
6. `auth.py:171`：`_claim_device_tasks` 认领时不做冲突检测（trial 限额 1，影响小）。
7. `tasks.py` `renew_task` 并发双击 read-modify-write（保守方向只 +30 天不叠加，可接受）。

### 前端 × 8
1. R10-U1：付费页 force 退款二次确认弹窗叠加——`payments/index.tsx:161-167` 两个 AlertDialog 同时 open。修复：弹 force 弹窗时先 `setRefunding(null)`。
2. R10-U2：灵动岛"监控中 · N 个任务"随 Home tab 过滤失真——切到"已过期"tab 后计数按过期列表算。修复：用独立 `api.tasks('active')` 或 App 层维护两份列表。
3. R10-U3："已过期"tab 一键续期成功后 notices 随卡片卸载丢失。修复：走 `location.state.notice` 首页横幅（AddMonitor 已有此模式）。
4. R10-U4：改密/注册无前端密码长度预校验，后端 422 英文直出。修复：前端校验 `>= 8` 中文提示。
5. R10-U5：登录页邮箱框回车无反应（只有密码框绑了 Enter）。修复：邮箱框同样绑定。
6. R10-U6：`/notify/test` 被拒文案自相矛盾——"该测试只验证目标是否连通"但实际 400 拒绝未执行。修复：改为"该通道当前档位不支持，测试未执行"。
7. R10-U7：流量页"累计 UV"把按日去重 UV 直接加总。修复：改名"各日 UV 之和"或后端给区间去重。
8. R10-U8：admin `req()` 401 跳转后继续执行抛错（toast 一闪）。修复：跳转后 throw 专用 `AuthExpiredError`，调用方静默吞掉。

### 前端死代码 × 4
1. `StockStateBadge.tsx:51`：`withDot` prop 零调用方。
2. `USE_MOCK=false` 后 5 个后台页面 `{USE_MOCK && '（mock 数据，待后端联调）'}` 死分支残留。
3. `Me.tsx:341`：匿名进"我的"时 `load()` 仍调 `api.payments()` 吃 401（无害，多一次请求）。
4. `AddMonitor.tsx:158`：UI-3 预检 `tasksUsed` 回退用 tab 过滤后列表（低频不准）。

### 部署/文档 × 7
1. hooks 不回填已有证书：`deploy.sh:213-231` 跳过 certbot 时，已有 renewal conf 旧版无 hooks 的机器修不了。建议 else 分支检查 renewal conf 是否含 pre_hook。
2. `deploy.sh:88`：`python3 -c 'import venv'` 挡不住缺 python3-venv（Debian 系拆包）。建议改查 `import ensurepip`。
3. CRLF 的 .env：`env_val` 剥 `\r` 但 `source` 不剥 → `APP_ENV="prod\r"` → is_prod False。建议 source 前清洗或拒绝。
4. 前端 dist 非原子替换：`rm -rf dist && cp -r` 窗口期 nginx 404。建议 build 到 `dist.new` 再 `mv`。
5. README 第 10 项"Clash"无支撑：`PROXY_POOL` 只支持 `http(s)://`。建议注明"Clash 本地 HTTP 代理地址填入 PROXY_POOL 即可"。
6. 恢复手册硬编码 DB 路径：假设默认 DATABASE_URL。建议提示"改过 DATABASE_URL 以 backup.sh 解析为准"。
7. `deploy.sh:163` `ln -sfn` 边缘：`/opt/stockmon/deploy` 已是真实目录时建到目录里面。建议先判断。

---

## 五、确认无问题的维度（附证据）

- **SQL 注入**：4 处 `text()` 全命名绑定参数；无 f-string 拼 SQL。
- **日志敏感信息**：grep 无密码/token/bark_key/邮箱明文；登录失败只记邮箱 sha256 前 16 位。
- **越权**：`_get_owned`（`tasks.py:525`）双归属校验；admin 路由全挂 `get_current_admin`；通知列表 `_mask_target` 脱敏。
- **失败≠无货铁律**：`AppleError → _mark_unknown`（`engine.py:502`，prev_known 不动）；grep 确认无失败路径写 `unavailable`。
- **Alembic**：单头 `d2e4f6a8b0c1`；`alembic check` → "No new upgrade operations detected"。
- **并发**：engine 单进程串行；sweep 只在 engine 跑；API `--workers 1`；webhook 先 grant 后 dedup 同一事务，duplicate 时 rollback 连带回滚。
- **限流**：注册 5/h/IP、登录 5 次锁、匿名创建 20/h/IP、resend-code 邮箱 3/h + IP 20/h、全局 60/min/IP；XFF 默认不信任。
- **验签 fail-closed**：`pay.py:146-158`；prod 缺关键变量拒绝启动。
- **时区**：naive UTC 入库 + `as_naive_utc` 归一化；展示 +8（除 P2 后端-2/-3 两处）。
- **异常吞没**：除 `engine.py:140` 外无 bare except / except-pass。
- **localStorage/XSS**：认证全程 HttpOnly Cookie；admin 仅存标记位。
- **ErrorBoundary**：`main.tsx:18-24` 包裹全站；异步路径均有 try/catch。
- **死代码**：AST 全量扫描无实质死代码（前端 4 处小死代码见上）。

---

## 六、已知遗留（不计入）

- 后-D-2 Bark 三选一：用户已拍板选①（按档位校验直接 400）；本轮 R10-I3/R10-U6 为其文案配套问题，已列入修复。
- 爱发电 webhook 签名算法真实对拍（等用户爱发电就绪 + 部署后联调，上线前唯一外部 blocker）。
- 对账定时 job TODO。
- 对标"可以晚点"：微信通知、3 秒刷新档、全站城市榜单、后台运营四件套、冷启动种子数据。

**结论：第十轮非零问题。** P0 × 1，P1 × 12，P2 × 26。
