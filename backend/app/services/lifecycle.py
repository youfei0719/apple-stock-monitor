"""任务/会员生命周期 sweep：过期提醒、续期、僵尸清理、无货提醒、会员到期降级。

由监控引擎每 tick 调用一次（见 engine.py）。各函数幂等、可重入：
- 提醒类通知按天去重（同一 user/task + kind 一天只记一条）；
- 僵尸里程碑去重持久化在 system_config；
- 所有生命周期通知均为审计/站内风格（channel="system"，status="sent"），
  不走外部通道、不扣推送配额。

全部时间 UTC（naive）。
"""

from datetime import datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from app.core.logging import get_logger
from app.core.tiers import TIERS, VALID_TIERS, effective_tier
from app.core.timeutil import utcnow
from app.models.models import (
    ApiHit,
    IdempotencyRecord,
    MonitorTask,
    Notification,
    QuotaUsage,
    StockState,
    SystemConfig,
    User,
)
from app.models.models import (
    Session as DbSession,
)
from app.services.engine import QUOTA_CYCLE_DAYS, quota_period_key
from app.services.notifier import build_product_link

log = get_logger("lifecycle")

# 僵尸任务里程碑（天）及去重 key
ZOMBIE_MILESTONES = (30, 60, 90)
ZOMBIE_NOTIFIED_KEY = "lifecycle:zombie_notified"

# 到期提醒里程碑：到期前 3 天 / 1 天
RENEW_MILESTONES = (3, 1)


def _utcnow() -> datetime:
    return utcnow()


def _today_start(now: datetime) -> datetime:
    """按天去重的"今天"起点：北京时间口径（R7：与 admin.py overview/traffic、
    analytics/history 的 "+8 hours" 按天口径一致。此前按 UTC 起点，用户看到
    的"今日"少 8 小时，会导致跨天边界的重复提醒）。

    输入输出均为 naive UTC 时间戳：北京时间午夜 = UTC 前一天 16:00。
    """
    beijing_midnight = (now + timedelta(hours=8)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return beijing_midnight - timedelta(hours=8)


def _milestone_3_1(days_left: float) -> int | None:
    """到期前 3 天/1 天里程碑判定（其他天数返回 None，不打扰）。"""
    if 2.0 < days_left <= 3.0:
        return 3
    if 0.0 <= days_left <= 1.0:
        return 1
    return None


def _beijing_date(dt: datetime | None) -> str:
    """UTC 时间转北京时间日期展示（R4-P2：通知文案不再用 UTC 裸格式）。"""
    return (dt + timedelta(hours=8)).strftime("%Y-%m-%d") if dt else ""


def converge_task_limit(
    db: Session, user: User, tasks: list[MonitorTask] | None = None, reason: str | None = None
) -> list[MonitorTask]:
    """按用户当前档位的任务上限暂停超限任务（复用 membership_sweep 的 keep_limit 口径）。

    R4-P1-B8：refund 把用户打回 free 后立即调用——membership_sweep 只处理
    standard/pro 过期用户，tier 已是 free 会被跳过，pro 的 30 个任务会继续
    轮询、吃 free 的配额。返回被暂停的任务列表。

    R5-F-N7：走 effective_tier（付费过期按 free 算），不再读 user.tier 原始值。
    R5-B-N3：调用方可传 tasks（已 selectinload 预取），避免每用户一次查询；
    不传则回退到单次 SQL（refund 等单用户场景）。
    """
    keep_limit = TIERS.get(effective_tier(user), TIERS["free"])["tasks_limit"]
    if tasks is None:
        active = (
            db.execute(
                select(MonitorTask)
                .where(MonitorTask.user_id == user.id, MonitorTask.paused.is_(False))
                .order_by(MonitorTask.updated_at.desc())
            )
            .scalars()
            .all()
        )
    else:
        active = sorted(
            (t for t in tasks if not t.paused),
            key=lambda t: t.updated_at or datetime.min,
            reverse=True,
        )
    paused = active[keep_limit:]
    for t in paused:
        t.paused = True
        # R6-I9：暂停原因落库，供 resume_tier_limited_tasks（tier_limit）/
        # resume_quota_exhausted_tasks（quota_exhausted）区分恢复
        if reason is not None:
            t.paused_reason = reason
        db.add(t)
    if paused:
        log.info("tasks_converged", user_id=user.id, tier=effective_tier(user), paused=len(paused))
    return paused


def null_notification_task_ids(db: Session, task_id: int) -> int:
    """删任务前把关联通知的 task_id 置 NULL（R9-I1 公共函数）。

    db.py 未设 PRAGMA foreign_keys=ON，SQLite 层 FK 的 ondelete="SET NULL"
    不会触发——必须显式置空，否则删任务后通知悬空、历史 join 丢数据。
    通知靠 part_number 快照列保留型号信息（见 B-N2）。tasks.delete_task 与
    task_expiry_sweep 的 trial 删除共用本函数。返回被置空的行数。
    """
    n = db.execute(
        update(Notification).where(Notification.task_id == task_id).values(task_id=None)
    ).rowcount
    if n:
        log.info("notification_task_ids_nulled", task_id=task_id, count=n)
    return n


def purge_task_idempotency_records(db: Session, scope: str, task_id: int) -> int:
    """删任务时清理 task_ids 含该任务 id 的幂等记录（R25-P2-1）。

    内容键/用户键记录的 task_ids 指向已删任务时整条删除——否则删任务后
    同内容立即重建（不同 key）会撞 uq_idempotency_scope_key，看到 task_ids
    非空的旧记录，TTL（IDEMPOTENCY_CLAIM_TTL_MIN=10 分钟）内走 409
    idempotency_in_progress（R24 引入的回归；删任务改渠道/改名后重建是
    用户常用流）。并发在途的记录 task_ids 为空，不受影响，R24 并发测试
    语义不变。tasks.delete_task 与 task_expiry_sweep 的 trial 删除共用。
    返回清理的记录数。
    """
    recs = (
        db.execute(
            select(IdempotencyRecord).where(IdempotencyRecord.scope == scope)
        )
        .scalars()
        .all()
    )
    n = 0
    for rec in recs:
        # task_ids 是 JSON 列，Python 层做成员判断（SQLite JSON contains
        # 方言不可靠）；只删"已指向被删任务"的记录，在途占位（task_ids 为空）不动
        if rec.task_ids and task_id in rec.task_ids:
            db.delete(rec)
            n += 1
    if n:
        db.flush()
        log.info("idempotency_records_purged", scope=scope, task_id=task_id, count=n)
    return n


def resume_tier_limited_tasks(db: Session, user: User) -> list[MonitorTask]:
    """R9-I14：档位提升/续费成功后，自动恢复因档位超限被暂停的任务。

    只恢复 paused_reason == "tier_limit" 的任务（降档时 converge_task_limit
    置的；manual/quota_exhausted 等其他原因的不动）；按新档位 tasks_limit
    限额、优先恢复最近更新的任务；恢复后清空 paused_reason。
    webhook / admin claim / admin 改档三条升级路径共用。返回被恢复的任务。
    """
    keep_limit = TIERS.get(effective_tier(user), TIERS["free"])["tasks_limit"]
    paused = (
        db.execute(
            select(MonitorTask)
            .where(
                MonitorTask.user_id == user.id,
                MonitorTask.paused.is_(True),
                MonitorTask.paused_reason == "tier_limit",
            )
            .order_by(MonitorTask.updated_at.desc())
        )
        .scalars()
        .all()
    )
    if not paused:
        return []
    active_count = db.execute(
        select(func.count())
        .select_from(MonitorTask)
        .where(MonitorTask.user_id == user.id, MonitorTask.paused.is_(False))
    ).scalar()
    slots = max(keep_limit - (active_count or 0), 0)
    resumed = paused[:slots]
    for t in resumed:
        t.paused = False
        t.paused_reason = None
        db.add(t)
    if resumed:
        log.info(
            "tier_limited_tasks_resumed",
            user_id=user.id,
            tier=effective_tier(user),
            resumed=len(resumed),
            still_paused=len(paused) - len(resumed),
        )
    return resumed


def resume_quota_exhausted_tasks(db: Session, user: User) -> list[MonitorTask]:
    """R13-P2-5：升级/续费成功后，自动恢复因配额耗尽被暂停的任务。

    只恢复 paused_reason == "quota_exhausted" 的任务（manual / tier_limit /
    notify_failures / zombie 等其他原因的不动——手动暂停必须由用户亲手恢复，
    与手动暂停严格区分）；要求新周期配额有余量（当前周期 push_count <
    档位 push_limit，与 engine._check_quota 同口径），否则不动，避免
    "恢复→引擎立刻再暂停→重复发 quota_exhausted 通知"的抖动；按新档位
    tasks_limit 限额、优先恢复最近更新的任务；恢复后清空 paused_reason。
    webhook / admin claim / admin 改档三条升级路径共用。返回被恢复的任务。
    """
    if user is None or user.id is None:
        return []
    tier = effective_tier(user)
    tier_cfg = TIERS.get(tier, TIERS["free"])
    # 配额余量检查：先滚锚点（升级路径一般已由 apply_tier_grant 置好新锚点，
    # 这里是兜底），再按周期键读用量
    now = utcnow()
    anchor = getattr(user, "quota_reset_at", None)
    rolled = anchor
    while rolled is not None and now >= rolled:
        rolled = rolled + timedelta(days=QUOTA_CYCLE_DAYS)
    if rolled != anchor:
        user.quota_reset_at = rolled
        db.add(user)
    period = quota_period_key(user)
    usage = db.execute(
        select(QuotaUsage).where(
            QuotaUsage.user_id == user.id, QuotaUsage.period == period
        )
    ).scalar_one_or_none()
    used = usage.push_count if usage else 0
    if used >= tier_cfg["push_limit"]:
        log.info(
            "quota_exhausted_tasks_not_resumed",
            user_id=user.id,
            tier=tier,
            used=used,
            limit=tier_cfg["push_limit"],
        )
        return []
    paused = (
        db.execute(
            select(MonitorTask)
            .where(
                MonitorTask.user_id == user.id,
                MonitorTask.paused.is_(True),
                MonitorTask.paused_reason == "quota_exhausted",
            )
            .order_by(MonitorTask.updated_at.desc())
        )
        .scalars()
        .all()
    )
    if not paused:
        return []
    active_count = db.execute(
        select(func.count())
        .select_from(MonitorTask)
        .where(MonitorTask.user_id == user.id, MonitorTask.paused.is_(False))
    ).scalar()
    slots = max(tier_cfg["tasks_limit"] - (active_count or 0), 0)
    resumed = paused[:slots]
    for t in resumed:
        t.paused = False
        t.paused_reason = None
        db.add(t)
    if resumed:
        log.info(
            "quota_exhausted_tasks_resumed",
            user_id=user.id,
            tier=tier,
            resumed=len(resumed),
            still_paused=len(paused) - len(resumed),
        )
    return resumed


def _sys_notify(
    db: Session,
    user_id: int | None,
    task_id: int | None,
    kind: str,
    title: str,
    body: str,
    link: str = "",
    part_number: str | None = None,
) -> Notification:
    """记一条审计风格的站内通知：不走外部通道，不扣配额。"""
    n = Notification(
        user_id=user_id,
        task_id=task_id,
        kind=kind,
        channel="system",
        target="",
        title=title,
        body=body,
        link=link,
        part_number=part_number,
        status="sent",
    )
    db.add(n)
    return n


def _notified_today_user_ids(db: Session, kind: str, user_ids: list[int]) -> set[int]:
    """R5-B-N3：一次查询拿 kind 今天已通知的用户 id 集合，替代每用户一次 count（消 N+1）。"""
    if not user_ids:
        return set()
    return {
        r
        for r in db.execute(
            select(Notification.user_id).where(
                Notification.kind == kind,
                Notification.user_id.in_(user_ids),
                Notification.created_at >= _today_start(_utcnow()),
            )
        ).scalars()
        if r is not None
    }


def _notified_today_task_ids(db: Session, kind: str, task_ids: list[int]) -> set[int]:
    """R5-B-N3：一次查询拿 kind 今天已通知的任务 id 集合，替代每任务一次 count（消 N+1）。"""
    if not task_ids:
        return set()
    return {
        r
        for r in db.execute(
            select(Notification.task_id).where(
                Notification.kind == kind,
                Notification.task_id.in_(task_ids),
                Notification.created_at >= _today_start(_utcnow()),
            )
        ).scalars()
        if r is not None
    }


def _get_kv(db: Session, key: str) -> dict:
    row = db.execute(select(SystemConfig).where(SystemConfig.key == key)).scalar_one_or_none()
    return dict(row.value) if row and isinstance(row.value, dict) else {}


def _set_kv(db: Session, key: str, value: dict) -> None:
    row = db.execute(select(SystemConfig).where(SystemConfig.key == key)).scalar_one_or_none()
    if row is None:
        row = SystemConfig(key=key, value=value)
        db.add(row)
    else:
        row.value = value
        db.add(row)


# ---------------------------------------------------------------- membership
def membership_sweep(db: Session) -> dict:
    """会员到期降级 + 到期前 3 天/1 天续费提醒 + 降级后超限任务暂停。"""
    now = _utcnow()
    stats = {
        "downgraded": 0,
        "pending_promoted": 0,
        "renewal_reminders": 0,
        "tasks_paused": 0,
    }
    # ---- 到期降级 ----
    # R5-B-N1：tier_expires_at 为 NULL 的付费用户也要回收（历史脏数据：以前 PATCH
    # 设付费档不强制要求到期时间，NULL 会永远逃过 tier_expires_at < now 的筛选）。
    # R5-B-N3：selectinload(User.tasks) 预取，converge_task_limit 直接用内存对象。
    expired = (
        db.execute(
            select(User)
            .options(selectinload(User.tasks))
            .where(
                User.tier.in_(["standard", "pro"]),
                # R6-D3：bootstrap 管理员永不参与降级 sweep（双保险；
                # bootstrap 侧已给 +10 年到期）
                User.is_admin.is_(False),
                or_(User.tier_expires_at < now, User.tier_expires_at.is_(None)),
            )
        )
        .scalars()
        .all()
    )
    for u in expired:
        old = u.tier
        new_exp: datetime | None = None
        new_quota: datetime | None = None
        # R23-P3-5：是否晋升先记旗标，计数器移到原子 UPDATE 成功之后
        # （并发 webhook 续费导致 rowcount=0 走 continue 时不再虚增）。
        promoted = u.pending_tier in VALID_TIERS
        if promoted:
            # 不自洽-1：降级到期生效——到期时按 pending_tier 切换
            new = u.pending_tier
            new_exp = now + timedelta(days=30)
            # R4-P1-D1：晋升 pending_tier 时同步配额锚点，否则配额周期与
            # 会员周期错位
            new_quota = now + timedelta(days=30)
            action = (
                f"已按预约切换为{TIERS[new]['name']}"
                f"（有效期至 {_beijing_date(new_exp)}，北京时间）"
            )
        else:
            new = "free"
            action = "已降为免费版（任务上限 3 个、每周期推送 5 次，周期为 30 天滚动）"
            # R11-P2-1：降 free 也置 now+30d（与 refund / 手动降档同口径），
            # 不再置 NULL（此前靠 ensure_quota_anchor 懒初始化，口径不一）。
            new_quota = now + timedelta(days=30)
        # R5-竞态-1：降级走原子 UPDATE，WHERE 带 tier_expires_at < now（+NULL）
        # 且 tier 未变的双重条件——select 与 commit 之间若 webhook 写入了续费
        # （tier_expires_at 已刷新为未来），WHERE 不命中、rowcount=0，本轮跳过，
        # 不会把刚续费的用户覆盖降级。rowcount 校验代替"先读后写"的乐观锁。
        result = db.execute(
            update(User)
            .where(
                User.id == u.id,
                User.tier == old,
                or_(User.tier_expires_at < now, User.tier_expires_at.is_(None)),
            )
            .values(tier=new, tier_expires_at=new_exp, pending_tier=None, quota_reset_at=new_quota)
        )
        if result.rowcount != 1:
            log.warning("membership_sweep_race_skipped", user_id=u.id)
            continue
        stats["pending_promoted" if promoted else "downgraded"] += 1
        # 把 ORM 对象与原子 UPDATE 后的行同步（后续 converge/通知读 u.tier 等字段）
        u.tier = new
        u.tier_expires_at = new_exp
        u.pending_tier = None
        u.quota_reset_at = new_quota
        # D7：按切换后档位的任务上限保留最近更新的 N 个任务，暂停超出部分
        # （pending_tier=standard 的用户切换后仍是 10 个限额，不是一刀切到 3 个）
        new_info = TIERS.get(u.tier, TIERS["free"])
        paused_tasks = converge_task_limit(db, u, tasks=u.tasks, reason="tier_limit")
        stats["tasks_paused"] += len(paused_tasks)
        paused_names = [t.name for t in paused_tasks]
        pause_note = (
            f"；超出{new_info['name']}版任务上限，已自动暂停 {len(paused_names)} 个任务："
            + "、".join(paused_names[:5])
            + ("…" if len(paused_names) > 5 else "")
            if paused_names
            else ""
        )
        _sys_notify(
            db,
            u.id,
            None,
            "membership_changed",
            f"会员已到期：{TIERS[old]['name']} → {TIERS[u.tier]['name']}",
            f"您的{TIERS[old]['name']}会员已到期，{action}{pause_note}。如需恢复请前往「我」页续费。",
        )
        log.info("membership_downgraded", user_id=u.id, old=old, new=u.tier)

    # ---- 到期前 3 天 / 1 天续费提醒（按天去重） ----
    upcoming = (
        db.execute(
            select(User).where(User.tier.in_(["standard", "pro"]), User.tier_expires_at > now)
        )
        .scalars()
        .all()
    )
    # R5-B-N3：一次查出今天已发过 membership_expiring 的用户，消 N+1
    expiring_notified = _notified_today_user_ids(
        db, "membership_expiring", [u.id for u in upcoming]
    )
    for u in upcoming:
        days_left = (u.tier_expires_at - now).total_seconds() / 86400
        ms = _milestone_3_1(days_left)
        if ms is None:
            continue
        if u.id in expiring_notified:
            continue
        _sys_notify(
            db,
            u.id,
            None,
            "membership_expiring",
            f"会员将于 {ms} 天后到期",
            f"您的{TIERS[u.tier]['name']}会员将于 "
            f"{_beijing_date(u.tier_expires_at)}（北京时间）到期，"
            f"到期后将降为免费版。如需续费请前往「我」页，提前续费不亏天数。",
        )
        stats["renewal_reminders"] += 1
        log.info("membership_expiring", user_id=u.id, days_left=round(days_left, 2))

    db.commit()
    return stats


# ---------------------------------------------------------------- task expiry
def task_expiry_sweep(db: Session) -> dict:
    """任务过期前 3 天/1 天提醒；trial 匿名任务过期 7 天后物理删除。"""
    now = _utcnow()
    stats = {"reminders": 0, "trial_deleted": 0}

    tasks = (
        db.execute(
            # R5-B-N3：selectinload 预取 states，消每任务一次 StockState 查询
            select(MonitorTask)
            .options(selectinload(MonitorTask.states))
            .where(
                MonitorTask.expires_at.is_not(None),
                MonitorTask.paused.is_(False),
            )
        )
        .scalars()
        .all()
    )
    # R5-B-N3：一次查出今天已发过 task_expiring 的任务，消 N+1
    expiring_notified = _notified_today_task_ids(db, "task_expiring", [t.id for t in tasks])
    for t in tasks:
        days_left = (t.expires_at - now).total_seconds() / 86400
        ms = _milestone_3_1(days_left)
        if ms is None:
            continue
        if t.id in expiring_notified:
            continue
        # R22-P3-2：匿名 trial 任务（user_id=None）的 task_expiring 提醒会写入
        # 通知行，但匿名用户调不了 /notifications，永远不可见（除非随后注册
        # 过户）——跳过，卡片已有"剩余 N 天"+续期按钮，不重复写无人读取的行。
        if t.user_id is None:
            continue
        _sys_notify(
            db,
            t.user_id,
            t.id,
            "task_expiring",
            f"监控任务将于 {ms} 天后到期：{t.name}",
            f"任务「{t.name}」（{t.part_number}）将于 "
            f"{_beijing_date(t.expires_at)}（北京时间）到期，"
            "到期后停止轮询、可在任务详情页一键续期（+30 天）。",
            link=build_product_link(t.category, t.part_number),
            part_number=t.part_number,
        )
        stats["reminders"] += 1
        log.info("task_expiring", task_id=t.id, days_left=round(days_left, 2))

    # trial 匿名任务过期 7 天后物理删除（断裂-7）
    cutoff = now - timedelta(days=7)
    old_trials = (
        db.execute(
            select(MonitorTask).where(
                MonitorTask.user_id.is_(None),
                MonitorTask.expires_at < cutoff,
            )
        )
        .scalars()
        .all()
    )
    for t in old_trials:
        # R9-I1：先置空再删（复用公共函数），否则通知悬空
        null_notification_task_ids(db, t.id)
        # R25-P2-1：同 delete_task，清理指向该任务的幂等记录
        purge_task_idempotency_records(
            db,
            f"u:{t.user_id}" if t.user_id is not None else f"d:{t.device_id}",
            t.id,
        )
        db.delete(t)
        stats["trial_deleted"] += 1
        log.info("trial_task_deleted", task_id=t.id)

    db.commit()
    return stats


# ---------------------------------------------------------------- zombie
def _zombie_days(
    states: list[StockState], task_created_at: datetime | None, now: datetime
) -> int | None:
    """连续无货天数：全部 state 持续非 available 的天数。

    R5-B-N3：调用方已 selectinload 预取 states，直接传列表，消每任务一次查询。

    - 任一门店 available → 0（不清零里程碑记录，只是不发提醒）
    - 混有 unknown/cooling/verifying → None（数据不可确认，本轮跳过）
    - 锚点 = 各门店最近一次有货事件（last_event_at），从未有货则按任务创建时间
    """
    if not states:
        return None
    state_set = {r.state for r in states}
    if "available" in state_set:
        return 0
    if not state_set <= {"unavailable"}:
        return None
    anchors = [r.last_event_at or task_created_at for r in states]
    anchors = [a for a in anchors if a]
    if not anchors:
        return None
    return (now - max(anchors)).days


def zombie_sweep(db: Session) -> dict:
    """连续无货 30/60/90 天各提醒一次（不扣配额）；90 天且 auto_retire 未关闭自动暂停。"""
    now = _utcnow()
    stats = {"nudges": 0, "auto_paused": 0}

    notified: dict[str, list[int]] = _get_kv(db, ZOMBIE_NOTIFIED_KEY)
    # 清理已不存在任务的记录
    alive_ids = {r for r in db.execute(select(MonitorTask.id)).scalars().all()}
    for tid in [k for k in notified if k.isdigit() and int(k) not in alive_ids]:
        del notified[tid]

    tasks = (
        db.execute(
            # R5-B-N3：selectinload 预取 states，_zombie_days 直接读内存，消 N+1
            select(MonitorTask)
            .options(selectinload(MonitorTask.states))
            .where(
                MonitorTask.paused.is_(False),
            )
        )
        .scalars()
        .all()
    )
    for t in tasks:
        # 已过期任务不轮询，僵尸计时冻结，跳过
        if t.expires_at is not None and t.expires_at < now:
            continue
        days = _zombie_days(t.states, t.created_at, now)
        if not days:
            continue
        done = notified.get(str(t.id), [])
        # 取本轮应提醒的最高里程碑（追赶场景一次只发一条，避免刷屏），
        # 同时把已达到的低里程碑一并标记为已通知。
        target = max(
            (ms for ms in ZOMBIE_MILESTONES if days >= ms and ms not in done),
            default=None,
        )
        if target is None:
            continue
        auto_retire = getattr(t, "auto_retire", True)
        body = (
            f"任务「{t.name}」（{t.part_number}）已连续 {days} 天无货。"
            "该型号可能已下架或长期无补货，建议检查是否继续监控。"
        )
        if target >= 90 and auto_retire:
            t.paused = True
            # R6-I9：暂停原因落库
            t.paused_reason = "zombie"
            db.add(t)
            body += "已按「自动结束」开关自动暂停，可在任务详情重新开启。"
            stats["auto_paused"] += 1
            log.info("zombie_auto_paused", task_id=t.id, days=days)
        elif target >= 90:
            body += "「自动结束」开关已关闭，未自动暂停。"
        _sys_notify(
            db,
            t.user_id,
            t.id,
            "zombie_nudge",
            f"长期无货提醒（{target} 天）：{t.name}",
            body,
            link=build_product_link(t.category, t.part_number),
            part_number=t.part_number,
        )
        notified[str(t.id)] = sorted(set(done) | {ms for ms in ZOMBIE_MILESTONES if days >= ms})
        stats["nudges"] += 1
        log.info("zombie_nudge", task_id=t.id, milestone=target, days=days)

    _set_kv(db, ZOMBIE_NOTIFIED_KEY, notified)
    db.commit()
    return stats


# ---------------------------------------------------------------- maintenance
# api_hits 保留策略（R4-P1-B4）：删除 90 天前的访问统计行，防止表无限增长 +
# 与引擎争 SQLite 写锁。
# 调度方式：并入 run_lifecycle_sweep（engine 每 ~5 分钟跑一轮 sweep），无需独立 cron。
# R6-P2-15：prune 每天最多执行一次（sweep 高频跑，重复全表 delete 是浪费）。
API_HITS_RETENTION_DAYS = 90
API_HITS_PRUNE_KEY = "api_hits_pruned_at"
NOTIFICATIONS_RETENTION_DAYS = 90
NOTIFICATIONS_PRUNE_KEY = "notifications_pruned_at"
# R9-O4：sessions 清理的每日去重 key
SESSIONS_PRUNE_KEY = "sessions_pruned_at"
# R23-P3-2：幂等键记录保留期——10 分钟 TTL 后即无用（重复请求只在短窗口重放），
# 保留 7 天兜底（排查用），之后删除，否则 idempotency_records 无限增长。
IDEMPOTENCY_RETENTION_DAYS = 7
IDEMPOTENCY_PRUNE_KEY = "idempotency_pruned_at"


def _beijing_date_str(now: datetime) -> str:
    """R10-P2-3：北京时间日期字符串（与 _today_start 的北京时间口径一致；
    此前按 UTC 日期去重，"每天一次"的边界差 8 小时）。"""
    return (now + timedelta(hours=8)).strftime("%Y-%m-%d")


def _pruned_today(db: Session, key: str) -> bool:
    """今天是否已执行过该 prune（R6-P2-15：每天一次）。"""
    return _get_kv(db, key).get("date") == _beijing_date_str(_utcnow())


def _mark_pruned(db: Session, key: str) -> None:
    _set_kv(db, key, {"date": _beijing_date_str(_utcnow())})


def prune_api_hits(db: Session, retention_days: int = API_HITS_RETENTION_DAYS) -> dict:
    """删除 retention_days 天前的 api_hits 行。幂等、可重入。"""
    if _pruned_today(db, API_HITS_PRUNE_KEY):
        return {"pruned": 0, "skipped": "daily"}
    cutoff = _utcnow() - timedelta(days=retention_days)
    n = db.query(ApiHit).filter(ApiHit.created_at < cutoff).delete(synchronize_session=False)
    _mark_pruned(db, API_HITS_PRUNE_KEY)
    db.commit()
    if n:
        log.info("api_hits_pruned", deleted=n, retention_days=retention_days)
    return {"pruned": n}


def prune_sessions(db: Session) -> dict:
    """R9-O4：sessions 清理——删除已过期的登录会话行（只增不减会无限增长）。

    按 expires_at < now 判定（不是固定保留期：未过期会话必须保留）。
    幂等、可重入，每天最多执行一次。
    """
    if _pruned_today(db, SESSIONS_PRUNE_KEY):
        return {"pruned": 0, "skipped": "daily"}
    n = (
        db.query(DbSession)
        .filter(DbSession.expires_at < _utcnow())
        .delete(synchronize_session=False)
    )
    _mark_pruned(db, SESSIONS_PRUNE_KEY)
    db.commit()
    if n:
        log.info("sessions_pruned", deleted=n)
    return {"pruned": n}


def prune_notifications(db: Session, retention_days: int = NOTIFICATIONS_RETENTION_DAYS) -> dict:
    """R6-P2-14：notifications 保留 retention_days 天，删更早的行（审计/重试
    用的旧行一并清理）。幂等、可重入，每天最多执行一次。"""
    if _pruned_today(db, NOTIFICATIONS_PRUNE_KEY):
        return {"pruned": 0, "skipped": "daily"}
    cutoff = _utcnow() - timedelta(days=retention_days)
    n = (
        db.query(Notification)
        .filter(Notification.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    _mark_pruned(db, NOTIFICATIONS_PRUNE_KEY)
    db.commit()
    if n:
        log.info("notifications_pruned", deleted=n, retention_days=retention_days)
    return {"pruned": n}


def prune_idempotency_records(
    db: Session, retention_days: int = IDEMPOTENCY_RETENTION_DAYS
) -> dict:
    """R23-P3-2：idempotency_records 保留 retention_days 天，删更早的行。

    幂等键 TTL 只有 10 分钟，过期记录纯粹占表；保留 7 天供排查。
    幂等、可重入，每天最多执行一次。"""
    if _pruned_today(db, IDEMPOTENCY_PRUNE_KEY):
        return {"pruned": 0, "skipped": "daily"}
    cutoff = _utcnow() - timedelta(days=retention_days)
    n = (
        db.query(IdempotencyRecord)
        .filter(IdempotencyRecord.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    _mark_pruned(db, IDEMPOTENCY_PRUNE_KEY)
    db.commit()
    if n:
        log.info("idempotency_records_pruned", deleted=n, retention_days=retention_days)
    return {"pruned": n}


# ---------------------------------------------------------------- entry
def run_lifecycle_sweep(db: Session) -> dict:
    """执行一轮生命周期 sweep，返回各项计数。幂等、可重入。

    单个子 sweep 异常不影响其他 sweep，也不影响引擎主轮询。
    """
    out: dict = {}
    for name, fn in (
        ("membership", membership_sweep),
        ("task_expiry", task_expiry_sweep),
        ("zombie", zombie_sweep),
        ("api_hits_retention", prune_api_hits),
        # R6-P2-14：notifications 保留期清理（90 天）
        ("notifications_retention", prune_notifications),
        # R9-O4：已过期登录会话清理
        ("sessions_retention", prune_sessions),
        # R23-P3-2：幂等键记录保留期清理（7 天）
        ("idempotency_retention", prune_idempotency_records),
    ):
        try:
            out[name] = fn(db)
        except Exception as e:  # noqa: BLE001 - sweep 失败只记日志，不中断 tick
            db.rollback()
            log.warning("lifecycle_sweep_failed", sweep=name, error=str(e))
            out[name] = {"error": str(e)[:200]}
    return out


__all__ = [
    "run_lifecycle_sweep",
    "membership_sweep",
    "task_expiry_sweep",
    "zombie_sweep",
    "converge_task_limit",
    "null_notification_task_ids",
    "resume_tier_limited_tasks",
    "resume_quota_exhausted_tasks",
    "prune_api_hits",
    "prune_notifications",
    "prune_sessions",
    "prune_idempotency_records",
]
