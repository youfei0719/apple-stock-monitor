# 第十三轮审查修复总结（P1×2、P2×5、P3×3，全清零）

修复日期：2026-10-09。审查报告中的 10 条全部修复并验证通过。

## P1（2 条）

### P1-1 一键续期被引擎轮询静默吞掉
- 根因：`renew_task` 用 `(now - task.updated_at) < 10s` 做并发双击去重；
  `updated_at` 带 `onupdate=_utcnow`，引擎每轮 poll（写 `last_polled_at` /
  `last_poll_ok` / `last_poll_ms`）都会推进它，pro 用户约 87% 概率续期无效，
  前端还显示"已续期至"假象。
- 修复：新增 `monitor_tasks.last_renewed_at` 专用列（migration
  `e4f5a6b7c8d9`，`alembic heads` 单 head 已验证，scratch 库升级实测建列成功），
  去重改判该列，仅续期成功时写入；引擎轮询不碰它。
  - `backend/app/models/models.py`、`backend/app/api/routers/tasks.py`
  - `backend/alembic/versions/e4f5a6b7c8d9_round13_renew_dedup_last_renewed_at.py`
- 前端 `frontend/src/pages/TaskDetail.tsx`：`gainedDays < 0.9` 且非匿名时改提示
  "请勿重复点击"（非匿名真实续期必 ≥29 天，走到该分支即命中去重），不再显示
  "已续期至"假象；匿名分支保持原样。
- 回归测试 `backend/tests/test_round13_renew_dedup.py`（3 用例全过）：
  引擎 poll 后续期真实生效；10 秒内双击仍被去重；窗口过后可再次续期。

### P1-2 后台"会员改级"降为 free/trial 默认路径必吃 400
- 根因：`openPending` 恒预填 +30 天，`confirmChange` 恒带 `tier_expires_at`，
  被后端 R11-P2-2 分支 400 拒绝。
- 修复（`admin/src/features/members/index.tsx`）：目标 tier 为 free/trial 时
  `openPending` 不预填（`setExpiresAt('')`），`confirmChange` 传 `undefined`
  （请求体省略该字段）；对话框里 free/trial 时禁用到期时间输入并提示
  "free/trial 档位无需到期时间"。
- 端到端验证（真实调 `patch_user`）：`{tier:"free"}` 不带到期时间 → 无 400、
  到期清空；升级 pro 带到期时间 → 正常；脏组合（free + 到期时间）→ 仍 400
 （后端防线保留）。

## P2（5 条）

### P2-3 后台日志 tail 写死 logs/app.log，生产恒为空
- `backend/app/api/routers/admin.py::_tail_log` 改按 `STOCKMON_LOG_NAME` 环境变量
  读对应文件（默认 `app.log`，与 `core/logging.py` 同口径），不存在时回退
  `logs/app.log`。生产 `stockmon-api.log` / `stockmon-engine.log` 终于能被 tail 到。

### P2-4 高峰模式文案失实
- 核实：`catalog.py` 无任何 peak 分支（"catalog 后台刷新暂停"不成立）；
  pro 优先排序是常态逻辑、非高峰模式特有效果（"pro 任务优先"误导）；
  唯一生效点是 `engine.py:281` trial/free 间隔 ×4。
- `admin/src/features/system/index.tsx`（2 处）、`admin/src/lib/admin-api.ts`
  注释改为诚实描述："trial / free 轮询间隔 ×4"。

### P2-5 付费/续费后 quota_exhausted 暂停的任务不自动恢复
- `backend/app/services/lifecycle.py` 新增 `resume_quota_exhausted_tasks`：
  只恢复 `paused_reason == "quota_exhausted"`（manual 等不动，与手动暂停严格区分）；
  要求新周期配额有余量（当前周期 `push_count < 档位 push_limit`，
  与 `engine._check_quota` 同口径），否则不动，避免恢复→立刻再暂停的抖动；
  按新档位 `tasks_limit` 限额、优先恢复最近更新的；恢复后清空 `paused_reason`。
- 三条升级/续费路径（爱发电 webhook `pay.py`、后台改级 `admin.py::patch_user`、
  后台认领 `admin.py::claim_payment`）在 `resume_tier_limited_tasks` 旁并列调用，
  恢复数写入各自 `notices`（三处响应均已确认返回 notices）。
- 回归测试 `backend/tests/test_round13_quota_resume.py`（3 用例全过）：
  有余量时只恢复 quota_exhausted；无余量不动；tasks_limit 占满时不恢复。

### P2-6 Me 页退款文案过度承诺
- `frontend/src/pages/Me.tsx`："退款成功后对应会员档位将被收回" →
  "退款成功后请联系客服处理，管理员确认后收回档位"（对账 job 未落地前不承诺自动收回）。

### P2-7 AddMonitor 预计月消耗漏乘机型数
- `frontend/src/pages/AddMonitor.tsx`：`monthlyEstimate` 乘上 `partNumbers.length`
 （批量生成 N 机型 × M 门店时此前的估算只有 1/N）。

## P3（3 条）

### P3-8 仓库根 ruff 全量 backend/ 报 16 errors（全在 alembic/）
- `ruff check --fix` 修掉 6 处（I001/UP035/UP007），手工换行修掉 11 处 E501
  （历史 migration 文件，仅格式调整，revision 链不受影响）。
- 现状：`ruff check backend/`（仓库根全量）与 `ruff check app`（deploy 门禁口径）
  均为零警告。

### P3-9 `_flush_api_hits` 同步阻塞事件循环
- `backend/app/main.py`：`_buffer_api_hit` 触发刷盘时改走
  `loop.run_in_executor(None, _flush_api_hits)`（线程池执行同步 DB I/O）；
  非 async 上下文降级为直接执行。`SessionLocal()` 在工作线程内新建会话，
  `_api_hit_lock` 为 `threading.Lock`，线程安全。

### P3-10 `catalog.py` refresh=1 用 user.is_admin 绕过 TOTP
- 刷新逻辑抽为 `enqueue_catalog_refresh(db, background_tasks, admin_user)`；
  新增 `POST /api/admin/catalog/refresh`（`get_current_admin`，含 TOTP 二次验证）；
  公开 `GET /catalog/stores?refresh=1` 改为 403（`use_admin_refresh`，指引走管理后台）。
  全仓库确认无 `stores(1)` 调用方；`app.main` 导入验证无循环依赖。

## 验证
- `ruff check backend/`（仓库根）零警告；`ruff check app`（deploy 门禁口径）零警告
- `pytest tests/`：145 passed（含新增 6 个回归用例）
- `alembic heads`：单 head `e4f5a6b7c8d9`；scratch 库 `upgrade head` 建列成功
- 前端 `npm run build`（tsc --noEmit + vite）通过；后台 `npm run build`（tsc -b + vite）通过
- P1-1：回归测试模拟引擎 poll 后续期真实生效（非误判跳过）
- P1-2：真实调用 `patch_user` 三场景验证（降级无 400 / 升级正常 / 脏组合仍 400）

## 未动项（按任务要求）
- 爱发电签名真实对拍（等用户侧就绪）、对账定时 job TODO、对标"可以晚点"项。
