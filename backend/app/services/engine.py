"""监控引擎：按 tier 刷新间隔调度、合并同轮请求、边沿触发、配额、冷却退避。

边沿触发规则（纯函数 evaluate_transition，可单元测试）：
- 无货 -> 有货：instant 直接通知；confirmed 需连续 2 轮 available 才通知。
- 持续有货：不重复通知（除非任务配了 repeat_interval_sec）。
- 失败 -> unknown：不触发、不改写 prev_known 基线、confirmed_count 清零。
- 首轮（无基线）-> 有货：静默建基线，不通知。
"""

import asyncio
import math
import random
import sys
import time
from collections import deque
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.core.tiers import effective_tier, effective_tier_of
from app.models.models import MonitorTask, Notification, QuotaUsage, StockState, SystemConfig
from app.services.apple_client import AppleClient, AppleError, AppleRateLimitError
from app.services.notifier import (
    Notifier,
    build_product_link,
    send_bark,
    send_email,
    send_sms,
    send_webhook,
)

log = get_logger("engine")

COOLDOWN_KEY = "apple_cooldown"
CONFIRMED_ROUNDS = 2
MAX_COOLDOWN_SEC = 3600

# ---- 通知失败重试（断裂-5） ----
MAX_NOTIFY_RETRIES = 3  # 含 tick 内立即重试 1 次 + 定时重试 2 次
# retry_count(已重试次数) -> 下次重试延迟秒数
RETRY_BACKOFF_SEC = {1: 30, 2: 120}

# ---- 高峰模式（对标-1）：system_config key ----
PEAK_MODE_KEY = "peak_mode"

# ---- 引擎心跳（第三轮审查 D1）：standalone engine 每 tick 把心跳写入 system_config，
# API 进程不跑引擎，healthz / admin/system 读这条 DB 心跳判活（60s 内 = running）。
ENGINE_HEARTBEAT_KEY = "engine_heartbeat"
ENGINE_HEARTBEAT_TTL_SEC = 60
# lifecycle sweep 降频（第三轮审查 N8）：每多少个 tick 跑一次
# （ENGINE_TICK_SEC=5s → 每 60 tick = 5 分钟）
LIFECYCLE_SWEEP_EVERY_N_TICKS = 60

# ---- 配额周期：购买日+30天滚动（不自洽-2 修完口径） ----
QUOTA_CYCLE_DAYS = 30


def quota_period_key(user) -> str:
    """当前配额周期键 = 锚点（下次重置日）日期。

    锚点滚动后键自动变化，老周期行自然失效，无需删除。
    """
    anchor = getattr(user, "quota_reset_at", None)
    return anchor.strftime("%Y-%m-%d") if anchor else "legacy"


def ensure_quota_anchor(db, user) -> None:
    """保证用户有配额锚点（注册/老用户首次访问时初始化：注册日=免费锚点）。

    初始化时直接 commit（GET 接口调用时也需要落库）。
    """
    if user is None:
        return
    if getattr(user, "quota_reset_at", None) is None:
        user.quota_reset_at = datetime.utcnow() + timedelta(days=QUOTA_CYCLE_DAYS)
        db.add(user)
        db.commit()


def evaluate_transition(
    prev_known: str | None, new_state: str, confirmed_count: int, mode: str
) -> tuple[str | None, bool, int]:
    """返回 (new_prev_known, should_notify, new_confirmed_count)。"""
    if new_state == "unknown":
        # 失败：绝不触发，也不污染基线；连续确认计数清零（保守）
        return prev_known, False, 0
    if new_state == "available":
        cc = confirmed_count + 1
        if prev_known == "available":
            return "available", False, cc  # 持续有货不重复
        if mode == "confirmed" and cc < CONFIRMED_ROUNDS:
            # 未达连续确认数：不推进 prev_known，保持"边沿待确认"
            return prev_known, False, cc
        if prev_known is None and mode == "instant":
            return "available", False, cc  # instant 首轮静默基线
        return "available", True, cc
    # new_state == "unavailable"
    return "unavailable", False, 0


def get_config(db, key: str, default: dict) -> dict:
    row = db.execute(select(SystemConfig).where(SystemConfig.key == key)).scalar_one_or_none()
    return dict(row.value) if row else dict(default)


def set_config(db, key: str, value: dict) -> None:
    row = db.execute(select(SystemConfig).where(SystemConfig.key == key)).scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(SystemConfig(key=key, value=value))
    db.commit()


def tier_interval(tier: str, settings) -> int:
    return settings.tier_intervals.get(tier or "free", settings.tier_intervals["free"])


def read_engine_status(db) -> dict:
    """从 system_config 读引擎心跳（供 API 进程的 healthz / admin/system 用）。

    API 进程不跑引擎，内存 engine.status() 恒为 stopped；standalone engine 每 tick
    把心跳写进 DB，这里按 60s 判活。
    """
    row = db.execute(
        select(SystemConfig).where(SystemConfig.key == ENGINE_HEARTBEAT_KEY)
    ).scalar_one_or_none()
    hb = dict(row.value) if row and isinstance(row.value, dict) else {}
    at_s = hb.get("at")
    last_hb = None
    running = False
    if at_s:
        try:
            last_hb = datetime.fromisoformat(str(at_s).rstrip("Z"))
            running = (datetime.utcnow() - last_hb).total_seconds() < ENGINE_HEARTBEAT_TTL_SEC
        except ValueError:
            pass

    def _iso(dt):
        return dt.isoformat() + "Z" if dt else None

    return {
        "running": running,
        "last_heartbeat": _iso(last_hb),
        "last_tick_at": _iso(last_hb),
        "rounds_total": hb.get("rounds_total", 0),
        "rounds_ok": hb.get("rounds_ok", 0),
        "last_error": hb.get("last_error"),
    }


class Engine:
    def __init__(self):
        self.settings = get_settings()
        self.client = AppleClient()
        self.running = False
        self.last_heartbeat: datetime | None = None
        self.last_tick_at: datetime | None = None
        self.last_error: str | None = None
        self.rounds_total = 0
        self.rounds_ok = 0
        self._task: asyncio.Task | None = None
        # lifecycle sweep 降频计数器（第三轮审查 N8）
        self._lifecycle_tick = 0
        # Apple 请求预算窗口（断裂-20）：key -> deque[timestamp]，60s 滚动窗口；
        # key="global" 为全局，其余为 "u:<user_id>" / "d:<device_id>"（trial）
        self._req_windows: dict[str, deque] = {}
        # 预算跳过累计计数（R4-P0-4：只记内存，不写 notifications 表）
        self.budget_skips_total = 0

    # ---- 生命周期 ----
    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop(), name="monitor-engine")
        log.info("engine_started")

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("engine_stopped")

    # ---- 主循环 ----
    async def _loop(self) -> None:
        while self.running:
            try:
                await asyncio.to_thread(self.tick)
            except Exception as e:
                self.last_error = str(e)
                log.error("engine_tick_failed", error=str(e))
            await asyncio.sleep(self.settings.ENGINE_TICK_SEC)

    def tick(self) -> None:
        self.last_heartbeat = datetime.utcnow()
        self.last_tick_at = self.last_heartbeat
        db = SessionLocal()
        try:
            # D1：心跳落库（API 进程据此判活；与 _clear_cooldown 等同库写入）
            set_config(
                db,
                ENGINE_HEARTBEAT_KEY,
                {
                    "at": self.last_heartbeat.isoformat() + "Z",
                    "rounds_total": self.rounds_total,
                    "rounds_ok": self.rounds_ok,
                    "last_error": self.last_error,
                },
            )
            from app.services.lifecycle import run_lifecycle_sweep  # 延迟 import 防循环引用

            # N8：lifecycle sweep 降频——每 60 tick（约 5 分钟）跑一次，
            # 不再每 5 秒全表扫描
            self._lifecycle_tick += 1
            if self._lifecycle_tick % LIFECYCLE_SWEEP_EVERY_N_TICKS == 0:
                run_lifecycle_sweep(db)
            self._retry_pending_notifications(db)
            if self._in_cooldown(db):
                return
            tasks = self._due_tasks(db)
            if not tasks:
                return
            groups = self._group_tasks(tasks)
            for (city, parts), group_tasks in groups.items():
                # R6-D1：同 tick 内若已进入冷却（本 tick 前一个分组刚被限流），
                # 其余分组不再打 Apple——否则退避被架空。
                if self._in_cooldown(db):
                    log.info("tick_stop_on_cooldown", city=city)
                    break
                if self._poll_group(db, city, parts, group_tasks):
                    # R6-D1：本分组被 Apple 限流 → 停止本轮其余分组
                    break
        finally:
            db.close()

    # ---- 调度 ----
    def _in_cooldown(self, db) -> bool:
        cd = get_config(db, COOLDOWN_KEY, {})
        until = cd.get("until", 0)
        if until and time.time() < until:
            return True
        return False

    def _due_tasks(self, db) -> list[MonitorTask]:
        now = datetime.utcnow()
        now_ts = time.time()
        self._prune_req_windows(now_ts)
        peak = self._peak_mode(db)
        q = (
            select(MonitorTask)
            .options(selectinload(MonitorTask.user))  # N9：消除逐任务查 user 的 N+1
            .where(MonitorTask.paused.is_(False))
            .where((MonitorTask.expires_at.is_(None)) | (MonitorTask.expires_at > now))
        )
        tasks = list(db.execute(q).scalars().all())
        due = []
        for t in tasks:
            tier = effective_tier(t.user)
            interval = tier_interval(tier, self.settings)
            if peak and tier in ("trial", "free"):
                # 高峰模式：trial/free 刷新间隔强制拉长 ×4（对标-1）
                interval *= 4
            jitter = random.uniform(0, self.settings.ENGINE_POLL_JITTER_SEC)
            if t.last_polled_at is None:
                due.append(t)
            elif (now - t.last_polled_at).total_seconds() >= interval + jitter:
                due.append(t)
        # Pro 优先通道：tier 越高越先轮询
        priority = {"pro": 0, "standard": 1, "free": 2, "trial": 3}
        due.sort(key=lambda t: priority.get(effective_tier(t.user), 3))
        # 请求预算：per-user 本轮计数 + 全局每分钟上限（断裂-20）
        return self._apply_budgets(db, due, now_ts)

    # ---- 请求预算（断裂-20） ----
    @staticmethod
    def _user_key(task: MonitorTask) -> str:
        if task.user_id is not None:
            return f"u:{task.user_id}"
        return f"d:{task.device_id or 'unknown'}"

    def _estimate_requests(self, task: MonitorTask) -> int:
        """该任务本轮预计 Apple 请求数 = ceil(门店数 / 每请求上限)。"""
        n = len(task.store_numbers or [])
        return max(1, math.ceil(n / self.settings.APPLE_MAX_STORES_PER_REQ))

    def _prune_req_windows(self, now_ts: float) -> None:
        cutoff = now_ts - 60
        for key in list(self._req_windows.keys()):
            dq = self._req_windows[key]
            while dq and dq[0] < cutoff:
                dq.popleft()
            if not dq:
                del self._req_windows[key]

    def _window_count(self, key: str) -> int:
        return len(self._req_windows.get(key, ()))

    def _note_apple_request(self, now_ts: float, user_keys: set[str]) -> None:
        dq = self._req_windows.setdefault("global", deque())
        dq.append(now_ts)
        for k in user_keys:
            self._req_windows.setdefault(k, deque()).append(now_ts)

    def _apply_budgets(
        self, db, due: list[MonitorTask], now_ts: float
    ) -> list[MonitorTask]:
        """请求预算过滤。R4-P0-4：被预算跳过的任务只记内存计数 + 日志，
        不再写 notifications 表（此前每 5 秒重复写行，表无限膨胀）。"""
        kept: list[MonitorTask] = []
        skipped = 0
        for t in due:
            tier = effective_tier(t.user)
            est = self._estimate_requests(t)
            ukey = self._user_key(t)
            # 1) 单用户预算：60s 窗口内计数 + 本轮预计 > 上限 → 跳过
            if self._window_count(ukey) + est > self.settings.per_user_req_limit(tier):
                log.warning(
                    "budget_skip_per_user", task_id=t.id, tier=tier, est=est
                )
                skipped += 1
                continue
            # 2) 全局预算：超限时 trial/free/standard 先降速（跳过），pro 优先
            if (
                self._window_count("global") + est
                > self.settings.GLOBAL_APPLE_REQ_PER_MIN
                and tier != "pro"
            ):
                log.warning("budget_skip_global", task_id=t.id, tier=tier, est=est)
                skipped += 1
                continue
            kept.append(t)
        if skipped:
            self.budget_skips_total += skipped
            log.info("budget_skips", skipped=skipped, total=self.budget_skips_total)
        return kept

    def _peak_mode(self, db) -> bool:
        return bool(get_config(db, PEAK_MODE_KEY, {}).get("enabled", False))

    @staticmethod
    def _group_tasks(
        tasks: list[MonitorTask],
    ) -> dict[tuple[str, tuple[str, ...]], list[MonitorTask]]:
        groups: dict[tuple[str, tuple[str, ...]], list[MonitorTask]] = {}
        for t in tasks:
            stores = t.stores or []
            city = str((stores[0].get("city") if stores else "") or "").strip().lower()
            key = (city, (t.part_number,))
            groups.setdefault(key, []).append(t)
        return groups

    # ---- 单组轮询 ----
    def _poll_group(
        self, db, city: str, parts: tuple[str, ...], tasks: list[MonitorTask]
    ) -> bool:
        """轮询一组任务。返回 True 表示本组被 Apple 限流（已进入冷却），
        调用方应停止本轮其余分组，不再打 Apple（R6-D1）。"""
        store_numbers: list[str] = []
        for t in tasks:
            for s in t.store_numbers or []:
                if s not in store_numbers:
                    store_numbers.append(s)
        max_per = self.settings.APPLE_MAX_STORES_PER_REQ
        chunks = [
            store_numbers[i : i + max_per] for i in range(0, len(store_numbers), max_per)
        ] or [[]]

        for chunk in chunks:
            started = time.time()
            try:
                self._note_apple_request(time.time(), {self._user_key(t) for t in tasks})
                result = self.client.query(list(parts), chunk)
                self.rounds_total += 1
                self.rounds_ok += 1
                self._clear_cooldown(db)
                lookup = {(r.store_number, r.part_number): r for r in result.results}
                for t in tasks:
                    self._process_task(db, t, parts, chunk, lookup)
                    t.last_polled_at = datetime.utcnow()
                    t.last_poll_ok = True
                    t.last_poll_ms = (time.time() - started) * 1000
                db.commit()
            except AppleRateLimitError as e:
                self.rounds_total += 1
                self.last_error = str(e)
                self._enter_cooldown(db, tasks, chunk)
                log.warning("engine_rate_limited", error=str(e))
                # R6-D1：返回 True 告诉 tick 停止本轮其余分组（之前只跳过本组
                # 的剩余 chunk，同 tick 其他分组仍会继续打 Apple，退避被架空）
                return True
            except AppleError as e:
                self.rounds_total += 1
                self.last_error = str(e)
                log.warning("engine_apple_error", error=str(e))
                for t in tasks:
                    self._mark_unknown(db, t, parts, chunk)
                    t.last_polled_at = datetime.utcnow()
                    t.last_poll_ok = False
                db.commit()
        return False

    def _process_task(self, db, task: MonitorTask, parts, chunk, lookup) -> None:
        now = datetime.utcnow()
        for part in parts:
            for store in chunk:
                r = lookup.get((store, part))
                new_state = r.state if r else "unknown"
                row = self._get_state(db, task.id, store, part)
                prev_known = row.prev_known
                new_prev, notify, cc = evaluate_transition(
                    prev_known, new_state, row.confirmed_count, task.mode
                )

                # 持续有货 + 可配重复间隔：到期再提醒一次
                if (
                    not notify
                    and new_state == "available"
                    and prev_known == "available"
                    and task.repeat_interval_sec
                    and row.last_event_at
                    and (now - row.last_event_at).total_seconds() >= task.repeat_interval_sec
                ):
                    notify = True

                row.state = new_state
                row.prev_known = new_prev
                row.confirmed_count = cc
                if r:
                    row.pickup_display = r.pickup_display
                    row.store_pick_eligible = r.store_pick_eligible
                    row.pickup_search_quote = r.pickup_search_quote
                if notify:
                    # 配额口径：按实际发送成功的通知条数扣减（断裂-5）
                    if self._check_quota(db, task):
                        sent, no_channel = self._fire(db, task, store, part, r)
                        self._consume_quota(db, task, sent)
                        if no_channel:
                            # R6-D4：零渠道/全 skipped → 记一条 skipped 审计行，
                            # 且不推进 last_event_at（边沿不被消费，下轮仍会
                            # 触发，避免静默丢失）
                            self._record_skipped(
                                db,
                                task,
                                store,
                                part,
                                channel="no_channel",
                                error="no_channels_configured",
                                title="未配置通知渠道，到货提醒未发送",
                            )
                        else:
                            row.last_event_at = now
                        db.add(row)
                        # R6-P2-21：通知行 + 配额扣减同一事务一次提交——崩溃时
                        # 要么都没落库、要么都落库，保证不漏扣配额
                        db.commit()
                        self._maybe_quota_warning(db, task)
                    else:
                        self._record_skipped(db, task, store, part)
                        # 配额耗尽：自动暂停 + 发耗尽通知（不扣配额，断裂-4/断裂-9）
                        if not task.paused:
                            task.paused = True
                            # R6-I9：暂停原因落库，供认领时区分自动恢复
                            task.paused_reason = "quota_exhausted"
                            db.add(task)
                            db.commit()
                            self._send_quota_exhausted(db, task)
                db.add(row)

    def _get_state(self, db, task_id: int, store: str, part: str) -> StockState:
        row = db.execute(
            select(StockState).where(
                StockState.task_id == task_id,
                StockState.store_number == store,
                StockState.part_number == part,
            )
        ).scalar_one_or_none()
        if row is None:
            row = StockState(task_id=task_id, store_number=store, part_number=part)
            db.add(row)
            db.flush()
        return row

    def _mark_unknown(self, db, task: MonitorTask, parts, chunk) -> None:
        for part in parts:
            for store in chunk:
                row = self._get_state(db, task.id, store, part)
                # 失败只标 unknown，不改 prev_known（evaluate_transition 同理）
                row.state = "unknown"
                row.confirmed_count = 0
                db.add(row)

    # ---- 配额（购买日+30天滚动；按实际发送成功条数扣减） ----
    def _roll_quota_anchor(self, db, user, now: datetime) -> None:
        """锚点滚动：now >= quota_reset_at 则清零并锚点 += 30天（可跨多周期）。"""
        ensure_quota_anchor(db, user)
        while user.quota_reset_at is not None and now >= user.quota_reset_at:
            user.quota_reset_at = user.quota_reset_at + timedelta(days=QUOTA_CYCLE_DAYS)
            db.add(user)

    def _get_usage(self, db, user_id: int, period: str) -> QuotaUsage:
        usage = db.execute(
            select(QuotaUsage).where(
                QuotaUsage.user_id == user_id, QuotaUsage.period == period
            )
        ).scalar_one_or_none()
        if usage is None:
            usage = QuotaUsage(user_id=user_id, period=period, push_count=0)
            db.add(usage)
            db.flush()
        return usage

    def _check_quota(self, db, task: MonitorTask) -> bool:
        """只检查不扣减。耗尽返回 False（调用方自动暂停任务，断裂-4）。"""
        if task.user_id is None or task.user is None:
            return self._check_trial_quota(db, task)
        user = task.user
        now = datetime.utcnow()
        self._roll_quota_anchor(db, user, now)
        usage = self._get_usage(db, user.id, quota_period_key(user))
        limit = effective_tier_of(user)["push_limit"]
        if usage.push_count >= limit:
            log.warning("quota_exceeded", user_id=user.id, task_id=task.id)
            return False
        return True

    def _consume_quota(self, db, task: MonitorTask, n: int) -> None:
        """按实际发送成功的通知条数扣减（断裂-5）；失败回滚不扣。"""
        if n <= 0:
            return
        if task.user_id is None or task.user is None:
            self._consume_trial_quota(db, task, n)
            return
        user = task.user
        self._roll_quota_anchor(db, user, datetime.utcnow())
        usage = self._get_usage(db, user.id, quota_period_key(user))
        usage.push_count += n
        db.add(usage)

    def _trial_quota_state(self, db, task: MonitorTask) -> tuple[int, int, str]:
        """返回 (used, limit, key)。匿名 trial 按 device_id 逐月计数。"""
        device_id = task.device_id or "unknown"
        period = datetime.utcnow().strftime("%Y-%m")
        key = f"trial_quota:{device_id}:{period}"
        used = int(get_config(db, key, {}).get("used", 0))
        limit = effective_tier_of(None)["push_limit"]
        return used, limit, key

    def _check_trial_quota(self, db, task: MonitorTask) -> bool:
        """匿名体验配额：按 device_id 逐月计数，只检查不扣减。"""
        used, limit, _key = self._trial_quota_state(db, task)
        if used >= limit:
            log.warning(
                "trial_quota_exceeded",
                device_id=task.device_id,
                task_id=task.id,
            )
            return False
        return True

    def _consume_trial_quota(self, db, task: MonitorTask, n: int) -> None:
        used, _limit, key = self._trial_quota_state(db, task)
        set_config(db, key, {"used": used + n})

    def _maybe_quota_warning(self, db, task: MonitorTask) -> None:
        """配额 80%/100% 预警（不扣配额；按周期+档位去重，断裂-4）。"""
        user = task.user
        if user is None or task.user_id is None:
            return
        usage = self._get_usage(db, user.id, quota_period_key(user))
        limit = effective_tier_of(user)["push_limit"]
        if limit <= 0:
            return
        ratio = usage.push_count / limit
        level = "100" if ratio >= 1.0 else ("80" if ratio >= 0.8 else None)
        if level is None:
            return
        key = f"quota_warning:{user.id}:{quota_period_key(user)}:{level}"
        if get_config(db, key, {}).get("sent"):
            return  # 本周期已发过，不重复
        set_config(db, key, {"sent": True, "at": datetime.utcnow().isoformat() + "Z"})
        tier_name = effective_tier(user)
        if level == "100":
            title = "推送配额已用完"
            body = (
                f"你的{effective_tier_of(user)['name']}档本月推送配额（{limit} 次）已用完，"
                "新配额将在下个周期开始时恢复。"
            )
        else:
            title = "推送配额已使用 80%"
            body = (
                f"你的{effective_tier_of(user)['name']}档本月推送配额已使用 "
                f"{usage.push_count}/{limit} 次（80%），请留意剩余次数。"
            )
        log.info("quota_warning", user_id=user.id, level=level, tier=tier_name)
        self._send_system_notice(db, task, "quota_warning", title, body)

    def _send_quota_exhausted(self, db, task: MonitorTask) -> None:
        """配额耗尽自动暂停通知（不扣配额，断裂-4/断裂-9）。"""
        if task.user_id is None:
            title = "体验推送已用完"
            body = (
                f"任务「{task.name}」：体验版 1 次推送已用完，任务已自动暂停。"
                "注册 / 升级会员后可手动恢复并继续监控。"
            )
        else:
            info = effective_tier_of(task.user)
            title = "推送配额已耗尽，任务已自动暂停"
            body = (
                f"任务「{task.name}」：{info['name']}档本月推送配额（{info['push_limit']} 次）"
                "已耗尽，任务已自动暂停。下个配额周期开始后可手动恢复，或升级档位。"
            )
        log.info("quota_exhausted_pause", task_id=task.id, user_id=task.user_id)
        self._send_system_notice(db, task, "quota_exhausted", title, body)

    def _send_system_notice(
        self, db, task: MonitorTask, kind: str, title: str, body: str
    ) -> None:
        """系统通知（配额预警/耗尽/自动暂停）：走用户邮箱直发，不扣配额。

        匿名 trial 无邮箱可达：只记账（status=skipped），不伪造发送成功。
        """
        user = task.user
        channels: dict = {}
        if user is not None and getattr(user, "email", None):
            channels = {"email": user.email}
        if channels:
            records = Notifier(db).dispatch(
                task.user_id, task.id, channels, title, body, "", kind=kind
            )
            for n in records:
                n.part_number = task.part_number
                db.add(n)
        else:
            db.add(
                Notification(
                    user_id=task.user_id,
                    task_id=task.id,
                    kind=kind,
                    channel="page",
                    target=task.device_id or "",
                    title=title,
                    body=body,
                    link="",
                    part_number=task.part_number,
                    status="skipped",
                    error="no_channel",
                )
            )
        db.commit()

    # ---- 通知触发 ----
    def _fire(self, db, task: MonitorTask, store: str, part: str, r) -> tuple[int, bool]:
        """发送到货通知。

        返回 (实际发送成功条数, no_channel)：
        - sent：断裂-5 按成功扣减的依据。
        - no_channel：True 表示零渠道/全 skipped（没有任何发送尝试）——
          调用方此时记一条 skipped 审计行且不推进 last_event_at（R6-D4）；
          发送尝试过但全失败时为 False（重试机制接管，last_event_at 照常推进）。

        - 失败 tick 内立即重试 1 次；仍失败记 retry_at 由后续 tick 重发（最多 3 次）。
        - 发通知时把 part_number 写入 Notification 快照字段（不自洽-4）。
        - consecutive_failures：全通道失败+1、全成功清零；>=10 自动暂停（断裂-6）。
        - R6-P2-21：本函数不单独 commit（dispatch 用 commit=False）；通知行与
          配额扣减由调用方 _process_task 在同一事务一次提交，崩溃不漏扣。
          例外：自动暂停分支的 _send_system_notice 自带 commit——该分支 sent
          恒为 0（全失败才暂停），不涉及配额扣减，无漏扣风险。
        """
        store_name = next(
            (s.get("name", store) for s in (task.stores or []) if s.get("number") == store), store
        )
        title = f"有货提醒：{task.product_name or task.part_number}"
        body = f"{store_name}（{store}）现可自提：{task.product_name or part}"
        if task.color or task.capacity:
            body += f"（{task.color} {task.capacity}）".strip()
        link = build_product_link(task.category, part)
        log.info("stock_event", task_id=task.id, store=store, part=part)
        records = Notifier(db).dispatch(
            task.user_id, task.id, task.channels or {}, title, body, link, commit=False
        )
        now = datetime.utcnow()
        for n in records:
            n.part_number = part
            db.add(n)
        # 失败 tick 内立即重试 1 次
        for n in records:
            if n.status == "failed" and (n.retry_count or 0) < MAX_NOTIFY_RETRIES:
                ok = self._resend_record(n)
                n.retry_count = (n.retry_count or 0) + 1
                if ok:
                    n.status = "sent"
                    n.error = None
                    n.retry_at = None
                else:
                    n.retry_at = now + timedelta(
                        seconds=RETRY_BACKOFF_SEC.get(n.retry_count, 600)
                    )
                db.add(n)
        sent = sum(1 for n in records if n.status == "sent")
        # R6-D4：零渠道（records 为空）或全部被档位拦截为 skipped → 没有任何
        # 发送尝试，调用方记 skipped 审计行且不推进 last_event_at
        no_channel = not records or all(n.status == "skipped" for n in records)
        if records:
            n_failed = sum(1 for n in records if n.status == "failed")
            if n_failed == len(records):
                task.consecutive_failures = (task.consecutive_failures or 0) + 1
            elif sent == len(records):
                task.consecutive_failures = 0
            # 部分成功：保持计数不变（不断裂-6 字面：只定义了全失败+1/全成功清零）
            db.add(task)
            if task.consecutive_failures >= 10 and not task.paused:
                task.paused = True
                # R6-I9：暂停原因落库，供认领时区分自动恢复
                task.paused_reason = "notify_failures"
                db.add(task)
                log.warning(
                    "task_auto_paused",
                    task_id=task.id,
                    consecutive_failures=task.consecutive_failures,
                )
                # 系统通知自带 commit；本分支 sent 恒为 0，不涉及配额
                self._send_system_notice(
                    db,
                    task,
                    "task_auto_paused",
                    "监控任务已自动暂停",
                    f"任务「{task.name}」连续 10 次通知发送失败，已自动暂停。"
                    "请检查通知渠道配置（Bark key / 邮箱 / webhook）后手动恢复任务。",
                )
        db.flush()
        return sent, no_channel

    # ---- 失败通知定时重试（断裂-5） ----
    @staticmethod
    def _resend_record(n: Notification) -> bool:
        """用通知行自带的 channel/target/title/body/link 重发一次。"""
        try:
            channel, target = n.channel, n.target
            if channel == "bark":
                send_bark(target, n.title, n.body, n.link)
            elif channel in ("wecom", "dingtalk", "feishu"):
                send_webhook(channel, target, n.title, n.body + f"\n{n.link}")
            elif channel == "email":
                send_email(target, n.title, f"{n.body}\n\n{n.link}")
            elif channel == "sms":
                send_sms(target, n.body)
            else:
                raise ValueError(f"unsupported channel: {channel}")
            log.info("notify_retry_ok", notification_id=n.id, channel=channel)
            return True
        except Exception as e:
            n.error = str(e)[:1024]
            log.warning(
                "notify_retry_failed", notification_id=n.id, channel=n.channel, error=str(e)
            )
            return False

    def _retry_pending_notifications(self, db) -> None:
        """每 tick 捞 retry_at<=now AND status='failed' 的通知重发（最多 3 次）。

        重试成功不重复扣配额（配额只在 _fire 返回的 sent 条数上扣）。
        """
        now = datetime.utcnow()
        rows = (
            db.execute(
                select(Notification)
                .where(Notification.status == "failed")
                .where(Notification.retry_at.is_not(None))
                .where(Notification.retry_at <= now)
                .where(Notification.retry_count < MAX_NOTIFY_RETRIES)
                .limit(100)
            )
            .scalars()
            .all()
        )
        for n in rows:
            ok = self._resend_record(n)
            n.retry_count = (n.retry_count or 0) + 1
            if ok:
                n.status = "sent"
                n.error = None
                n.retry_at = None
            else:
                n.retry_at = now + timedelta(
                    seconds=RETRY_BACKOFF_SEC.get(n.retry_count, 600)
                )
            db.add(n)
        if rows:
            db.commit()
            log.info("notify_retry_batch", retried=len(rows))

    def _record_skipped(
        self,
        db,
        task: MonitorTask,
        store: str,
        part: str,
        channel: str = "quota",
        error: str = "quota_exceeded",
        title: str | None = None,
    ) -> None:
        db.add(
            Notification(
                user_id=task.user_id,
                task_id=task.id,
                kind="stock_alert",
                channel=channel,
                target="",
                title=title
                or ("已跳过推送" if channel == "budget" else "配额耗尽，已跳过推送"),
                body=f"task={task.id} store={store} part={part}",
                link="",
                part_number=part or None,
                status="skipped",
                error=error,
            )
        )

    # ---- 冷却 / 退避（持久化） ----
    def _enter_cooldown(self, db, tasks: list[MonitorTask], chunk: list[str]) -> None:
        cd = get_config(db, COOLDOWN_KEY, {})
        level = int(cd.get("level", 0))
        wait = min(self.settings.APPLE_COOLDOWN_BASE_SEC * (2**level), MAX_COOLDOWN_SEC)
        set_config(db, COOLDOWN_KEY, {"until": time.time() + wait, "level": level + 1})
        for t in tasks:
            for part in {t.part_number}:
                for store in chunk:
                    row = self._get_state(db, t.id, store, part)
                    row.state = "cooling"
                    db.add(row)
            t.last_polled_at = datetime.utcnow()
            t.last_poll_ok = False
            db.add(t)
        db.commit()
        log.warning("cooldown_entered", wait_sec=wait, level=level + 1)

    def _clear_cooldown(self, db) -> None:
        cd = get_config(db, COOLDOWN_KEY, {})
        if cd.get("level"):
            set_config(db, COOLDOWN_KEY, {"until": 0, "level": 0})


engine = Engine()


async def _amain() -> None:
    """独立进程入口（deploy/stockmon-engine.service 的 ExecStart 目标）。

    常驻运行监控引擎主循环，直到收到 SIGINT/SIGTERM 后优雅停止。

    R4-P0-1：fail-fast 检查 ENGINE_ENABLED。开关关闭时直接退出并打印说明，
    防止有人在 shell 手动 `python -m app.services.engine` 绕过开关启动
    第二个引擎（双引擎重复轮询/重复通知）。
    """
    # R6-I6：独立引擎进程必须初始化日志（文件落到 logs/app.log），否则
    # admin 的 log_tail 看不到引擎日志
    configure_logging()
    if not get_settings().ENGINE_ENABLED:
        print("ENGINE_ENABLED=false：拒绝启动监控引擎。")
        print("引擎只能由独立进程 stockmon-engine.service（ENGINE_ENABLED=true）运行；")
        print("API 进程内不再启动引擎，手动 python -m app.services.engine 也不会绕过开关。")
        sys.exit(1)
    await engine.start()
    try:
        await asyncio.Event().wait()
    finally:
        await engine.stop()


if __name__ == "__main__":
    asyncio.run(_amain())
