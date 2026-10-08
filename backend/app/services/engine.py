"""监控引擎：按 tier 刷新间隔调度、合并同轮请求、边沿触发、配额、冷却退避。

边沿触发规则（纯函数 evaluate_transition，可单元测试）：
- 无货 -> 有货：instant 直接通知；confirmed 需连续 2 轮 available 才通知。
- 持续有货：不重复通知（除非任务配了 repeat_interval_sec）。
- 失败 -> unknown：不触发、不改写 prev_known 基线、confirmed_count 清零。
- 首轮（无基线）-> 有货：静默建基线，不通知。
"""

import asyncio
import random
import time
from datetime import datetime

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.core.tiers import tier_of
from app.models.models import MonitorTask, Notification, QuotaUsage, StockState, SystemConfig
from app.services.apple_client import AppleClient, AppleError, AppleRateLimitError
from app.services.notifier import Notifier, build_product_link

log = get_logger("engine")

COOLDOWN_KEY = "apple_cooldown"
CONFIRMED_ROUNDS = 2
MAX_COOLDOWN_SEC = 3600


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

    def status(self) -> dict:
        stale = True
        if self.last_heartbeat:
            stale = (datetime.utcnow() - self.last_heartbeat).total_seconds() > 60

        def _iso(dt):
            return dt.isoformat() + "Z" if dt else None

        return {
            "running": self.running and not stale,
            "last_heartbeat": _iso(self.last_heartbeat),
            "last_tick_at": _iso(self.last_tick_at),
            "rounds_total": self.rounds_total,
            "rounds_ok": self.rounds_ok,
            "last_error": self.last_error,
        }

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
            if self._in_cooldown(db):
                return
            tasks = self._due_tasks(db)
            if not tasks:
                return
            groups = self._group_tasks(tasks)
            for (city, parts), group_tasks in groups.items():
                self._poll_group(db, city, parts, group_tasks)
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
        q = (
            select(MonitorTask)
            .where(MonitorTask.paused.is_(False))
            .where((MonitorTask.expires_at.is_(None)) | (MonitorTask.expires_at > now))
        )
        tasks = list(db.execute(q).scalars().all())
        due = []
        for t in tasks:
            tier = t.user.tier if t.user else "trial"
            interval = tier_interval(tier, self.settings)
            jitter = random.uniform(0, self.settings.ENGINE_POLL_JITTER_SEC)
            if t.last_polled_at is None:
                due.append(t)
            elif (now - t.last_polled_at).total_seconds() >= interval + jitter:
                due.append(t)
        # Pro 优先通道：tier 越高越先轮询
        priority = {"pro": 0, "standard": 1, "free": 2, "trial": 3}
        due.sort(key=lambda t: priority.get(t.user.tier if t.user else "trial", 3))
        return due

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
    def _poll_group(self, db, city: str, parts: tuple[str, ...], tasks: list[MonitorTask]) -> None:
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
                return  # 本轮其余 chunk 全部跳过
            except AppleError as e:
                self.rounds_total += 1
                self.last_error = str(e)
                log.warning("engine_apple_error", error=str(e))
                for t in tasks:
                    self._mark_unknown(db, t, parts, chunk)
                    t.last_polled_at = datetime.utcnow()
                    t.last_poll_ok = False
                db.commit()

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
                    if self._check_quota(db, task):
                        self._fire(db, task, store, part, r)
                        row.last_event_at = now
                    else:
                        self._record_skipped(db, task, store, part)
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

    # ---- 配额 ----
    def _check_quota(self, db, task: MonitorTask) -> bool:
        if task.user_id is None:
            return True  # 匿名体验不计配额（靠前端/设备指纹限制任务数）
        period = datetime.utcnow().strftime("%Y-%m")
        usage = db.execute(
            select(QuotaUsage).where(
                QuotaUsage.user_id == task.user_id, QuotaUsage.period == period
            )
        ).scalar_one_or_none()
        if usage is None:
            usage = QuotaUsage(user_id=task.user_id, period=period, push_count=0)
            db.add(usage)
            db.flush()
        limit = tier_of(task.user.tier if task.user else "free")["push_limit"]
        if usage.push_count >= limit:
            log.warning("quota_exceeded", user_id=task.user_id, task_id=task.id)
            return False
        usage.push_count += 1
        db.add(usage)
        return True

    # ---- 通知触发 ----
    def _fire(self, db, task: MonitorTask, store: str, part: str, r) -> None:
        store_name = next(
            (s.get("name", store) for s in (task.stores or []) if s.get("number") == store), store
        )
        title = f"有货提醒：{task.product_name or task.part_number}"
        body = f"{store_name}（{store}）现可自提：{task.product_name or part}"
        if task.color or task.capacity:
            body += f"（{task.color} {task.capacity}）".strip()
        link = build_product_link(task.category, part)
        log.info("stock_event", task_id=task.id, store=store, part=part)
        Notifier(db).dispatch(task.user_id, task.id, task.channels or {}, title, body, link)

    def _record_skipped(self, db, task: MonitorTask, store: str, part: str) -> None:
        db.add(
            Notification(
                user_id=task.user_id,
                task_id=task.id,
                kind="stock_alert",
                channel="quota",
                target="",
                title="配额耗尽，已跳过推送",
                body=f"task={task.id} store={store} part={part}",
                link="",
                status="skipped",
                error="quota_exceeded",
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
