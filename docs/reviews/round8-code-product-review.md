# 第八轮代码审查 + 产品审查报告

- HEAD：`34d6c30`（第七轮修复后）
- 审查方式：三路并行（后端 / 前台+后台 / 部署+文档），只读审查，凡能实测的都实测
- 结论：**未达到零问题**。断裂 4 条（必须修），不自洽 17 条，UI 问题 12 条。

---

## 一、断裂（阻塞上线/部署）× 4

### R8-B0-1 · `deploy/stockmon-engine.service:11`：`Environment ENGINE_ENABLED=true` 缺等号，systemd 静默丢弃，引擎永不启动
- 依据：指令与变量之间是空格、无等号。本机 systemd 255 实测 `systemd-analyze verify` 报 `Unknown key name 'Environment ENGINE_ENABLED' in section 'Service', ignoring.`，该行被静默丢弃。
- 传导链：engine 进程的 `ENGINE_ENABLED` 只能来自 `EnvironmentFile=/opt/stockmon/.env` → `.env.example` 默认 `ENGINE_ENABLED=false` → `engine.py:872-876` fail-fast `sys.exit(1)` 拒绝启动 → `Restart=always` 无限重启 → `deploy.sh` 健康检查 60 秒等不到 `"engine":"running"` → `DEPLOY FAILED`。
- 修复：改为 `Environment=ENGINE_ENABLED=true`。同文件第 14 行 `STOCKMON_LOG_NAME` 的写法就是对的。

### R8-B0-2 · `deploy/stockmon-api.service:13`：`Environment ENGINE_ENABLED=false` 同样缺等号，D1 防误配落空
- 同上实测，该行被 systemd 忽略。当前行为碰巧正确（config 默认 `false`），但若用户在 `.env` 误写 `ENGINE_ENABLED=true`，本应被 unit 覆盖拦截，实际不会 → 双引擎重复轮询/重复通知/配额 double-count。
- 修复：改为 `Environment=ENGINE_ENABLED=false`。

### R8-B0-3 · `backend/app/api/routers/admin.py:221,227`：admin 补单 `tier_expires_at` 传 tz-aware 会入库错乱（偏差 8 小时）——钱相关
- 依据：pydantic 把 `"2026-12-01T08:00:00+08:00"` 解析为 tz-aware；SQLite DATETIME 方言写 tz-aware 时静默丢弃 tzinfo、不换算 UTC。
- 实测（调真实 `patch_user`，`/tmp/test_admin_tzaware.py`）：传 `2026-12-01T08:00:00+08:00`（意图 UTC 0 点）→ 入库 naive `2026-12-01T08:00:00`（被当 UTC），偏差 8:00:00；`quota_reset_at` 同样错。会员到期时间和配额周期锚点同时错 8 小时。
- 说明：后-D-1 只修了 tasks.py 用户侧入口，admin 侧是同类漏洞的漏网之鱼。`AdminUserPatchEx.tier_expires_at`（`admin.py:41`）未做归一化。
- 修复：把 `_as_naive_utc` 提升到公共模块（如 `app/core/timeutil.py`），在 `patch_user` 开头加归一化；补 tz-aware 输入测试用例。

### R8-B0-4 · `frontend/src/pages/Me.tsx`（logout 约 246-249 行）+ `frontend/src/components/App.tsx`（约 100-122 行）：退出登录后身份/任务状态残留
- 依据：`logout` 只做 `await api.logout(); navigate('/login', {replace:true})`，App 层的 `me`/`tasks` state 未清，AppContext 也没暴露 logout 方法。
- 后果链：用户 A 退出 → 登录页点浏览器后退回 `/me` → 渲染用户 A 的会员卡 → `load()` 401 → "出错了"错误页；直接访问 `/` → Home 显示用户 A 的旧任务列表，用户误以为仍登录有效，点进详情才 401。
- 修复：App 层增加 `logout()`（`setMe(null); setTasks([]); setTasksError(null)` 后调 `api.logout()`），Me 页改用它。

---

## 二、不自洽 × 17

### 部署/文档（5）
- **R8-I-1** `deploy.sh:26-63`/`backup.sh:64-87` 支持 `export KEY=` 前缀，但 systemd EnvironmentFile 静默丢弃整行（真机实测 `QUX_EXPORTED=<UNSET>`）。用户写 `export APP_SECRET_KEY=...` 时 deploy.sh 校验通过、服务实际读不到 → 回退默认值。修复：deploy.sh 在 .env 校验阶段 grep 拦截 `^[[:space:]]*export[[:space:]]` 行并报错（fail-fast）。
- **R8-I-2** `deploy/backup.sh:109`：sqlite3 本体命令是裸命令，失败时 `set -e` 直接终结脚本，绕过 `fail()`/`alert()`（实测 DST=/proc/1：EXIT=1 但无 `backup FAIL:`、无 alert 调用）。备份命令本身失败（磁盘满/权限）时运维收不到邮件。修复：`sqlite3 ... || fail "sqlite 备份命令失败（exit=$?）"`，或开头加 ERR trap。
- **R8-I-3** `deploy.sh:160` 注释 + 运维手册"轮转"说明：structlog 日志实际落在 `/opt/stockmon/backend/logs/`（`logging.py:12` 默认 `log_dir="logs"` + systemd WorkingDirectory），不在 `/opt/stockmon/logs/`；`logrotate-stockmon.conf` 只覆盖后者（好在 TimedRotatingFileHandler 自带 30 天轮转，无磁盘风险）。修复：修正注释+手册，写明两类日志真实路径（建议只改文档，别动 log_dir，copytruncate 与 TimedRotatingFileHandler 混用有截断风险）。
- **R8-I-4** `docs/运维手册.md:61` 行号漂移：写"第 152 行"，实际 `deploy.sh:154`。修复：更新或去掉行号引用。
- **R8-I-5** `.env.example` 缺 `BACKUP_ALERT_TO`（`backup.sh:20` 第一优先级收件人）。修复：加注释行（config.py 不用加，运维层键，app 不读）。

### 后端（3）
- **R8-I-6** `backend/app/api/routers/admin.py:348-420`：`claim_payment` 无订单状态守卫。只校验 `user_id is not None` 和 `tier_to` 合法性，对 `status` 无检查。`refund_payment` 允许对未认领订单标记 refunded，`close_payment` 语义是"不予开通直接关闭"，但之后调 `claim_payment` 仍会开通 30 天——已退款/已关闭订单可被认领成幽灵会员。修复：开头加 `if p.status not in ("paid","amount_mismatch","unknown_plan"): raise 400 bad_status`。
- **R8-I-7** `backend/app/api/routers/notify.py:79`：`/notify/test` 每日限额用 UTC 零点，全库其他按天口径已统一为北京时间（lifecycle/admin/analytics/history）。北京时间 0:00–8:00 的测试会计入"昨天"。修复：复用 `lifecycle._today_start`。
- **R8-I-8** `backend/app/api/routers/admin.py:153`：用户搜索 `User.email.like(f"%{q}%")` 未转义通配符（`history.py:52-56` 已做转义）。搜 `%` 匹配全部用户（admin-only 低危，无注入）。修复：照抄 history.py 转义逻辑。附带：`admin.py:198-201` 默认+30天时审计 `changes["tier_expires_at"]` 的 from 硬编码 None，旧值可能是过期时间戳，from 不准。

### 前台+后台（9）
- **R8-I-9** `admin/src/features/payments/index.tsx:50-52`：`canClose` 含 `r.status==='paid'`，但后端 `close_payment` 明确拒绝 paid（400 `use_refund_flow`），只允许 amount_mismatch/unknown_plan。管理员对 paid 点"关闭"必吃 400，且 `confirmClose` 没处理该 code。修复：`canClose` 改为 `['amount_mismatch','unknown_plan'].includes(r.status)` + 修正撒谎注释。
- **R8-I-10** `admin/src/features/payments/index.tsx:215`：认领按钮对 unknown_plan 显示，但后端 `admin.py:375-376` 要求 `tier_to ∈ ("standard","pro")` 否则 400 `bad_tier`。修复：按钮条件追加 `['standard','pro'].includes(r.tier_to)`。
- **R8-I-11** `frontend/src/pages/Me.tsx:225-243`：`Promise.all([plans, quota, payments, siteConfig])` 任一失败全页 ErrorState，用户最关心的配额/到期日因不相关的付费记录接口抖动而消失。修复：四个接口独立 try/catch，至少 quota/plans 与 payments 解耦。
- **R8-I-12** `frontend/src/pages/Home.tsx:153-170` vs `TaskDetail.tsx:127`：TaskCard 只有 expired 才有"一键续期"，TaskDetail 是 `expired || days<=3`。同一规则两处 UI 不一致。修复：TaskCard 在 expiringSoon 时也渲染续期按钮。
- **R8-I-13** 后台读失败静默：`overview/index.tsx:106`、`system/index.tsx:116-118`（catch→setData(null)→无限骨架屏）；`traffic/index.tsx:35`、`members/index.tsx:119`、`payments/index.tsx:94`（catch→空数组→"暂无数据"伪装）。修复：统一加错误态，区分"加载失败"与"无数据"。
- **R8-I-14** `admin/src/features/payments/index.tsx:111`："成功到账合计"基于当前筛选 `rows` 计算，切"只看待认领"时合计跟着变，误导收入认知。修复：单独请求全量或标注"当前视图合计"。
- **R8-I-15** 前台无修改密码入口：`Me.tsx` 无入口，后端 `auth.py:440` `PATCH /me` 已实现。修复：Me 页加改密入口。
- **R8-I-16** `frontend/src/pages/Home.tsx:105-113`：`togglePause` 无 catch，暂停/恢复失败零反馈（unhandled rejection）。修复：加 catch 行内错误文案。
- **R8-I-17** 三处过期注释：`Me.tsx:231-232`（说需后端配合 effective_tier，后端 `auth.py:425` 早已返回）；`admin-api.ts:63-65`（说 list_users 未返回 email_verified，`admin.py:163` 已返回）；`admin-profile.tsx:27`（TODO 真实登出，`/api/auth/logout` 已实现）。修复：更新注释。

---

## 三、UI 问题 × 12

- **R8-U-1** `frontend/src/components/NotifyChannels.tsx:137,159`：测试记录时间戳 `toLocaleString` 未传 `timeZone`，后端返回 UTC 带 Z；`Me.tsx`/`History.tsx` 均显式 `Asia/Shanghai`。修复：补 `timeZone: 'Asia/Shanghai'`。
- **R8-U-2** 前台管理员 TOTP 无后台入口：`Login.tsx` totp_required 只报错"请前往后台登录"，不给链接。修复：加"前往后台登录"按钮。
- **R8-U-3** 后台付费表"套餐"列裸显示 plan id（`payments/index.tsx:241-243`）。修复：映射中文。
- **R8-U-4** 后台系统页请求重复：`system/index.tsx:116` 的 `getLogTail()` 内部又调 `getSystem()`，`/api/admin/system` 请求两次。修复：直接取 `sys.log_tail`。
- **R8-U-5** 后台审计"操作"列裸英文 action（`system/index.tsx:348-352`）。修复：映射中文。
- **R8-U-6** 后台会员搜索无防抖（`members/index.tsx:114-119`），每击键一次请求。修复：300ms 防抖。
- **R8-U-7** 后台流量图表缺天无 0 填充，折线断裂。修复：前端按 days 补 0。
- **R8-U-8** 前台删除任务用原生 `window.confirm`（`Home.tsx:115`）。修复：换应用内确认。
- **R8-U-9** 前台无退款指引：付费记录有"已退款"状态，但无"如何退款"说明（退款走爱发电）。修复：加一行说明。
- **R8-U-10** `TaskDetail.tsx:185` 用 `saveMsg.includes('已保存')` 判颜色，文本匹配脆弱。修复：用布尔状态。
- **R8-U-11** 后台 mock 数据含真实邮箱 `youfei0719@gmail.com`（`admin-api.ts:111`，死代码但入库）。修复：换 `example.com`。
- **R8-U-12** 后台路由守卫每次进入调 `/api/admin/overview`（`route.tsx:36`），每页多一次请求。修复：缓存守卫结果。

---

## 四、回归验证通过（证据）

- 后-D-1 `_as_naive_utc()`：三种输入语义正确，create/patch/renew 三入口全覆盖，`tests/test_round7_backend.py` 四个用例通过 ✅
- 后-I-1 分支逻辑：只 PATCH tier_expires_at 时同步 quota_reset_at，R6-I5/tier=free 分支一致，测试通过 ✅（但输入层 tz-aware 漏网 → 本轮 R8-B0-3）
- 迁移 d2e4f6a8b0c1：`alembic heads` 单头，临时库 upgrade/downgrade 双向跑通，`alembic check` 无漂移 ✅
- 日志方案(a)：双 unit 均配 `STOCKMON_LOG_NAME`，手册同步 ✅（注：ENGINE_ENABLED 行的等号 bug 是另一行，不影响本项）
- backup.sh SMTP 告警链：`BACKUP_ALERT_TO > SSL_EMAIL > SMTP_USER`，未配时记日志+exit 1 非静默 ✅（但 sqlite3 本体失败路径绕过 → 本轮 R8-I-2）
- 认领 notice：三端链路完整，state 清掉后刷新不重复弹 ✅
- N14-B：前端标注 + 后端 test 档位校验，前后端一致 ✅
- 匿名试用链路：`api.createTask` 走单任务接口，避开 batch 401 ✅
- 预计送货日期：三处源码 grep 零命中 ✅；README 19 项清单 + 砍掉标注 ✅
- deploy.sh prod 预检两陷阱（行尾注释/export 前缀）：`env_val` 实测全部通过 ✅
- certbot 顺序：cp conf → stop → certonly --standalone → start → nginx -t，顺序正确 ✅
- 前端契约抽查：`/me`、`/quota`、`/plans`、`/payments`、`/notify/*`、`/history/releases`、`/analytics/*`、admin 全接口字段对齐 ✅；时区标注前后台一致 ✅
- 并发/异常/日志 PII/越权/XFF/限流/webhook fail-closed：全 ✅

## 五、不计入（已知遗留 / 待拍板）

- 后-D-2（Bark 三选一）：用户还没拍板
- 爱发电签名真实对拍、对账定时 job、对标"可以晚点"项

## 六、修复优先级建议

1. P0：R8-B0-1、R8-B0-2（两行 unit 加等号）→ R8-B0-3（admin tz 归一化）→ R8-B0-4（logout 清 state）
2. P1：R8-I-6（claim 状态守卫）、R8-I-9/R8-I-10（后台按钮误渲染）、R8-I-1（export 拦截）、R8-I-2（backup 加 fail）、R8-I-11（Me 解耦加载）
3. P2：其余不自洽 + 12 个 UI
