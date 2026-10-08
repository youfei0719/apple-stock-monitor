"""监控任务 CRUD + 批量生成 + 状态查询。

匿名体验：无会话时可用 X-Device-Id 创建 1 个任务（不计配额）。
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import _client_ip, get_current_user, get_optional_user
from app.api.errors import APIError
from app.core.db import get_db
from app.core.logging import get_logger
from app.core.ratelimit import check_rate_limit
from app.core.tiers import effective_tier, effective_tier_of, tier_of
from app.models.models import IdempotencyRecord, MonitorTask, Notification, StockState, User
from app.schemas import TaskBatchIn, TaskCreateIn, TaskOut, TaskPatchIn

router = APIRouter(prefix="/tasks", tags=["tasks"])
log = get_logger("tasks")

DISPLAY_STATE_ORDER = (
    "available",
    "unavailable",
    "unknown",
    "verifying",
    "cooling",
    "paused",
    "expired",
)

# 匿名体验任务最长存活 24h（服务端强制，不信任客户端传的 expires_at）
TRIAL_MAX_TTL = timedelta(hours=24)
# 任务创建接口 IP 维度限流：20 次/小时/IP
TASK_CREATE_LIMIT = 20
TASK_CREATE_WINDOW_SEC = 3600


def _as_naive_utc(dt: datetime | None) -> datetime | None:
    """R7 断裂 后-D-1：pydantic 会把 "2026-12-01T00:00:00Z" 解析为 tz-aware
    datetime，aware 与 naive 直接比较（expires_at < now / expires_at > cap）
    会 TypeError→500。入库/比较前统一归一化为 naive UTC（aware 转 UTC 后
    去 tzinfo，naive 保持原样）。"""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _clamp_expires(expires_at: datetime | None, anonymous: bool) -> datetime | None:
    """匿名任务强制 24h 过期：为空或超过 24h 时一律 clamp 到 now+24h。"""
    if not anonymous:
        return _as_naive_utc(expires_at)
    expires_at = _as_naive_utc(expires_at)
    cap = datetime.utcnow() + TRIAL_MAX_TTL
    if expires_at is None or expires_at > cap:
        return cap
    return expires_at


def _is_expired(task: MonitorTask, now: datetime | None = None) -> bool:
    """任务是否已过期：expires_at < now 且未手动暂停（断裂-7）。"""
    if task.paused:
        return False
    if task.expires_at is None:
        return False
    return task.expires_at < (now or datetime.utcnow())


def _display_state(task: MonitorTask, row: StockState | None) -> str:
    if task.paused:
        return "paused"
    if _is_expired(task):
        return "expired"
    if row is None:
        return "unknown"
    if row.state == "cooling":
        return "cooling"
    if row.state == "available" and task.mode == "confirmed" and row.confirmed_count < 2:
        return "verifying"
    return row.state if row.state in DISPLAY_STATE_ORDER else "unknown"


def _task_out(task: MonitorTask, db: Session) -> TaskOut:
    # R4-P1-B5：list_tasks 用 selectinload 预加载 states，这里直接读关系，
    # 其他入口（create/get）走懒加载，不再每任务一次 StockState 查询
    rows = task.states
    by_store: dict[str, dict] = {}
    for r in rows:
        by_store.setdefault(r.store_number, {})[r.part_number] = {
            "state": _display_state(task, r),
            "pickup_display": r.pickup_display,
            "store_pick_eligible": r.store_pick_eligible,
            "pickup_search_quote": r.pickup_search_quote,
            "updated_at": r.updated_at.isoformat() + "Z" if r.updated_at else None,
        }
    available_n = sum(1 for r in rows if _display_state(task, r) == "available")
    return TaskOut(
        id=task.id,
        name=task.name,
        group=task.group,
        category=task.category,
        part_number=task.part_number,
        product_name=task.product_name,
        color=task.color,
        capacity=task.capacity,
        stores=task.stores or [],
        mode=task.mode,
        repeat_interval_sec=task.repeat_interval_sec,
        channels=task.channels or {},
        paused=task.paused,
        expires_at=task.expires_at,
        auto_retire=getattr(task, "auto_retire", True),
        created_at=task.created_at,
        latest={"stores": by_store, "available_count": available_n, "total": len(rows)},
    )


def _check_task_limit(db: Session, user: User | None, device_id: str | None) -> None:
    if user:
        # 过期付费档按 free 算（断裂-1），走 effective_tier
        tier = effective_tier_of(user)
        n = db.execute(
            select(func.count()).select_from(MonitorTask).where(MonitorTask.user_id == user.id)
        ).scalar()
        if n >= tier["tasks_limit"]:
            raise APIError(403, f"任务数已达上限（{tier['tasks_limit']}）", "task_limit")
    else:
        n = db.execute(
            select(func.count()).select_from(MonitorTask).where(MonitorTask.device_id == device_id)
        ).scalar()
        if n >= tier_of("trial")["tasks_limit"]:
            raise APIError(403, "体验版仅可创建 1 个任务，请登录后使用", "task_limit_trial")


def _find_conflict(
    db: Session,
    user_id: int | None,
    device_id: str | None,
    part_number: str,
    store_numbers: list[str],
) -> MonitorTask | None:
    """断裂-8：按 (归属, part_number, 门店集合) 查重，避免重复任务重复通知/扣配额。

    N7：两侧门店号都做 strip().upper() 归一化再比较，大小写/空格不一致不产生重复任务。
    """
    q = select(MonitorTask)
    if user_id is not None:
        q = q.where(MonitorTask.user_id == user_id)
    else:
        q = q.where(MonitorTask.device_id == device_id, MonitorTask.user_id.is_(None))
    want = frozenset((s or "").strip().upper() for s in store_numbers)
    part_norm = (part_number or "").strip().upper()
    for t in db.execute(q).scalars().all():
        have = frozenset((s or "").strip().upper() for s in (t.store_numbers or []))
        if (t.part_number or "").strip().upper() == part_norm and have == want:
            return t
    return None


def _conflict_error(task: MonitorTask) -> APIError:
    return APIError(
        409,
        f"已存在相同监控任务（id={task.id}）：同型号 {task.part_number} + 同门店组合",
        "task_conflict",
    )


def _validate_task_in(data: TaskCreateIn) -> None:
    if data.mode not in ("instant", "confirmed"):
        raise APIError(400, "mode 必须为 instant 或 confirmed", "bad_mode")
    if data.category not in ("iphone", "ipad", "mac", "watch"):
        raise APIError(400, "category 非法", "bad_category")


def _validate_expires_at(expires_at: datetime | None) -> None:
    """R6-P2-12：expires_at 早于当前时间直接 400（过期任务建了也无意义）。

    R7 断裂 后-D-1：先归一化为 naive UTC 再比较（pydantic 解析出的 "…Z"
    是 tz-aware，与 datetime.utcnow() 直接比会 TypeError→500）。"""
    expires_at = _as_naive_utc(expires_at)
    if expires_at is not None and expires_at < datetime.utcnow():
        raise APIError(400, "expires_at 不能早于当前时间", "bad_expires_at")


def _channels_empty(channels: dict | None) -> bool:
    """渠道是否全空：无 bark_key/email 且 webhooks 为空。"""
    ch = channels or {}
    if (ch.get("bark_key") or "").strip() or (ch.get("email") or "").strip():
        return False
    webhooks = ch.get("webhooks") or []
    return not any(
        isinstance(w, dict) and (w.get("url") or "").strip() for w in webhooks
    )


def _require_channels(channels: dict, user: User | None) -> None:
    """R6-D4：非 trial 档任务必须配至少一个通知渠道——空渠道的到货边沿会
    被静默消费（零通知零记录）。trial 走站内 page 触达，不要求外部渠道。"""
    if _channels_empty(channels) and effective_tier(user) != "trial":
        raise APIError(
            400,
            "请至少配置一个通知渠道（邮箱 / Bark / 群机器人），否则到货时无法通知你",
            "channels_required",
        )


# ---- 幂等键（R6-P2-10） ----
IDEMPOTENCY_KEY_HEADER = "idempotency-key"
IDEMPOTENCY_KEY_MAXLEN = 128
# 占住 key 后超过此时长仍未写回结果：视为首个请求创建失败，允许接管
IDEMPOTENCY_CLAIM_TTL_MIN = 10


def _idempotency_scope(user: User | None, device_id: str | None) -> str:
    return f"u:{user.id}" if user else f"d:{device_id}"


def _lookup_idempotency(db: Session, scope: str, key: str) -> IdempotencyRecord | None:
    return db.execute(
        select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope, IdempotencyRecord.key == key
        )
    ).scalar_one_or_none()


def _claim_idempotency_key(
    db: Session, scope: str, key: str
) -> tuple[IdempotencyRecord, bool]:
    """占住幂等键。返回 (record, 是否由本次请求占住)。

    并发同 key 撞唯一约束时 IntegrityError → 回滚后取现存记录（不抛 500）。
    首个请求占住 key 后若超过 IDEMPOTENCY_CLAIM_TTL_MIN 仍未写回结果
    （创建中途失败/崩溃），本次请求接管该 key；仍在创建中则返回 (记录, False)，
    调用方按 409 idempotency_in_progress 处理。
    """
    rec = IdempotencyRecord(scope=scope, key=key, task_ids=[])
    db.add(rec)
    try:
        db.flush()
        return rec, True
    except IntegrityError:
        db.rollback()
        rec = _lookup_idempotency(db, scope, key)
        if (
            rec is not None
            and not rec.task_ids
            and rec.created_at is not None
            and datetime.utcnow() - rec.created_at
            > timedelta(minutes=IDEMPOTENCY_CLAIM_TTL_MIN)
        ):
            db.delete(rec)
            db.flush()
            new_rec = IdempotencyRecord(scope=scope, key=key, task_ids=[])
            db.add(new_rec)
            db.flush()
            log.warning("idempotency_claim_takeover", scope=scope)
            return new_rec, True
        return rec, False


def _idempotent_replay(
    db: Session,
    user: User | None,
    device_id: str | None,
    scope: str,
    key: str,
) -> list[TaskOut] | None:
    """重复 key：直接返回首次创建的任务（R6-P2-10）。无记录返回 None。"""
    rec = _lookup_idempotency(db, scope, key)
    if not rec or not rec.task_ids:
        return None
    out = []
    for tid in rec.task_ids:
        task = db.get(MonitorTask, tid)
        if task is None:
            # R7 诚实注释（此前写"按新请求处理"不成立）：这里返回 None 后，上游
            # _claim_idempotency_key 撞唯一约束会看到 task_ids 非空的记录——
            # TTL（IDEMPOTENCY_CLAIM_TTL_MIN=10 分钟）内不会接管，直接走 409
            # idempotency_in_progress；TTL 过期后才允许接管 key 重建。
            return None
        # scope 已隔离归属（u: / d: 前缀），这里只做兜底一致性检查
        if user and task.user_id != user.id:
            return None
        if not user and not (task.user_id is None and task.device_id == device_id):
            return None
        out.append(_task_out(task, db))
    return out


@router.get("", response_model=list[TaskOut])
def list_tasks(
    status: str = Query(default="all", description="active=监控中, expired=已过期, all=全部"),
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None),
):
    if status not in ("active", "expired", "all"):
        raise APIError(400, "status 必须为 active、expired 或 all", "bad_status")
    # R4-P1-B5：selectinload 预加载 states，_task_out 不再每任务查一次 StockState
    q = (
        select(MonitorTask)
        .options(selectinload(MonitorTask.states))
        .order_by(MonitorTask.created_at.desc())
    )
    if user:
        q = q.where(MonitorTask.user_id == user.id)
    elif x_device_id:
        q = q.where(MonitorTask.device_id == x_device_id)
    else:
        return []
    tasks = db.execute(q).scalars().all()
    if status == "active":
        tasks = [t for t in tasks if not _is_expired(t)]
    elif status == "expired":
        tasks = [t for t in tasks if _is_expired(t)]
    return [_task_out(t, db) for t in tasks]


@router.post("", response_model=TaskOut, status_code=201)
def create_task(
    data: TaskCreateIn,
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None),
):
    if not user and not x_device_id:
        raise APIError(401, "匿名创建任务需要 X-Device-Id 头", "device_required")
    ip = _client_ip(request)
    if not check_rate_limit(
        f"task_create:{ip}", limit=TASK_CREATE_LIMIT, window_sec=TASK_CREATE_WINDOW_SEC
    ):
        raise APIError(429, "创建任务过于频繁，请稍后再试", "rate_limited")
    # R6-P2-10：幂等键——重复 key 直接返回首次创建的任务（网络重试/重复提交
    # 不再 check-then-insert 建出重复任务）
    idem_key = (request.headers.get(IDEMPOTENCY_KEY_HEADER) or "").strip()[
        :IDEMPOTENCY_KEY_MAXLEN
    ]
    scope = _idempotency_scope(user, x_device_id)
    if idem_key:
        replayed = _idempotent_replay(db, user, x_device_id, scope, idem_key)
        if replayed:
            return replayed[0]
    _validate_task_in(data)
    _validate_expires_at(data.expires_at)
    _check_task_limit(db, user, x_device_id)
    stores = [s.model_dump() for s in data.stores]
    part_number = data.part_number.strip().upper()
    # N7：门店号归一化（strip+upper）后再查重/入库，大小写或空格不一致不产生重复任务
    store_numbers = [s["number"].strip().upper() for s in stores]
    channels = data.channels.model_dump(exclude_none=True)
    _require_channels(channels, user)
    # 断裂-8：重复任务冲突检测
    conflict = _find_conflict(
        db, user.id if user else None, x_device_id, part_number, store_numbers
    )
    if conflict:
        raise _conflict_error(conflict)
    idem_rec = None
    if idem_key:
        idem_rec, owned = _claim_idempotency_key(db, scope, idem_key)
        if not owned:
            # 并发同 key：首个请求已占住——有结果直接返回，否则说明还在创建中
            replayed = _idempotent_replay(db, user, x_device_id, scope, idem_key)
            if replayed:
                return replayed[0]
            raise APIError(409, "相同的幂等键正在处理中，请稍后重试", "idempotency_in_progress")
    task_kwargs: dict = dict(
        user_id=user.id if user else None,
        device_id=None if user else x_device_id,
        name=data.name,
        group=data.group,
        category=data.category,
        part_number=part_number,
        product_name=data.product_name,
        color=data.color,
        capacity=data.capacity,
        store_numbers=store_numbers,
        stores=stores,
        mode=data.mode,
        repeat_interval_sec=data.repeat_interval_sec,
        channels=channels,
        expires_at=_clamp_expires(data.expires_at, anonymous=user is None),
        auto_retire=data.auto_retire,
    )
    task = MonitorTask(**task_kwargs)
    db.add(task)
    db.commit()
    db.refresh(task)
    if idem_rec is not None:
        idem_rec.task_ids = [task.id]
        db.add(idem_rec)
        db.commit()
    log.info("task_created", task_id=task.id, user_id=user.id if user else None)
    return _task_out(task, db)


@router.post("/batch", response_model=list[TaskOut], status_code=201)
def batch_create(
    data: TaskBatchIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """门店 × 型号批量生成任务。"""
    # R6-P2-10：幂等键——重复 key 直接返回首次批量创建的任务
    idem_key = (request.headers.get(IDEMPOTENCY_KEY_HEADER) or "").strip()[
        :IDEMPOTENCY_KEY_MAXLEN
    ]
    scope = _idempotency_scope(user, None)
    if idem_key:
        replayed = _idempotent_replay(db, user, None, scope, idem_key)
        if replayed:
            return replayed
    # R4-P1-B6：复用 _validate_task_in 校验 mode + category（此前只判 mode，
    # category 非法会直接入库）
    _validate_task_in(data)
    tier = effective_tier_of(user)
    existing = db.execute(
        select(func.count()).select_from(MonitorTask).where(MonitorTask.user_id == user.id)
    ).scalar()
    combos = [(p, s) for p in data.part_numbers for s in data.store_numbers]
    if existing + len(combos) > tier["tasks_limit"]:
        raise APIError(
            403,
            f"批量生成将超出任务上限（{tier['tasks_limit']}），本次 {len(combos)} 个",
            "task_limit",
        )
    # 断裂-8：批量内去重 + 与已有任务查重（409）；N7：门店号归一化后再查重
    seen: set[tuple[str, frozenset]] = set()
    combos_norm = [(part.strip().upper(), store.strip().upper()) for part, store in combos]
    for pn, sn in combos_norm:
        key = (pn, frozenset([sn]))
        if key in seen:
            raise APIError(409, f"批量内重复：{pn} × {sn} 出现了多次", "task_conflict")
        seen.add(key)
        conflict = _find_conflict(db, user.id, None, pn, [sn])
        if conflict:
            raise _conflict_error(conflict)
    channels = data.channels.model_dump(exclude_none=True)
    # R6-D4：非 trial 档任务必须配通知渠道
    _require_channels(channels, user)
    idem_rec = None
    if idem_key:
        idem_rec, owned = _claim_idempotency_key(db, scope, idem_key)
        if not owned:
            replayed = _idempotent_replay(db, user, None, scope, idem_key)
            if replayed:
                return replayed
            raise APIError(409, "相同的幂等键正在处理中，请稍后重试", "idempotency_in_progress")
    created = []
    for pn, sn in combos_norm:
        name = data.name_template.replace("{part_number}", pn).replace("{store_number}", sn)
        task = MonitorTask(
            user_id=user.id,
            name=name,
            category=data.category,
            part_number=pn,
            store_numbers=[sn],
            stores=[{"number": sn, "name": "", "city": ""}],
            mode=data.mode,
            channels=channels,
        )
        db.add(task)
        created.append(task)
    db.commit()
    for t in created:
        db.refresh(t)
    if idem_rec is not None:
        idem_rec.task_ids = [t.id for t in created]
        db.add(idem_rec)
        db.commit()
    log.info("task_batch_created", user_id=user.id, count=len(created))
    return [_task_out(t, db) for t in created]


def _get_owned(task_id: int, user: User | None, device_id: str | None, db: Session) -> MonitorTask:
    task = db.get(MonitorTask, task_id)
    if not task:
        raise APIError(404, "任务不存在", "not_found")
    if user and task.user_id == user.id:
        return task
    if not user and device_id and task.device_id == device_id and task.user_id is None:
        return task
    raise APIError(403, "无权操作该任务", "forbidden")


@router.patch("/{task_id}", response_model=TaskOut)
def patch_task(
    task_id: int,
    data: TaskPatchIn,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None),
):
    task = _get_owned(task_id, user, x_device_id, db)
    # R4-P2：先校验后 setattr（此前 mode 非法时已 setattr，commit 前才 400，
    # 虽未落库但对象状态已脏）
    if data.mode is not None and data.mode not in ("instant", "confirmed"):
        raise APIError(400, "mode 必须为 instant 或 confirmed", "bad_mode")
    if data.expires_at is not None:
        # R6-P2-12：拒绝过去时间
        _validate_expires_at(data.expires_at)
    if data.channels is not None:
        # R6-D4：非 trial 档任务不允许把渠道清空（空渠道的到货边沿会被静默消费）
        _require_channels(data.channels.model_dump(exclude_none=True), user)
    if data.paused is not None:
        # R6-I9：手动暂停/恢复同步暂停原因——恢复时清空原因，避免脏原因残留
        task.paused_reason = "manual" if data.paused else None
    fields = ("name", "group", "paused", "mode", "repeat_interval_sec")
    for field in fields:
        v = getattr(data, field)
        if v is not None:
            setattr(task, field, v)
    if data.channels is not None:
        task.channels = data.channels.model_dump(exclude_none=True)
    if data.auto_retire is not None:
        task.auto_retire = data.auto_retire
    if data.expires_at is not None:
        # 匿名任务同样强制 24h 上限
        task.expires_at = _clamp_expires(data.expires_at, anonymous=user is None)
    db.add(task)
    db.commit()
    db.refresh(task)
    return _task_out(task, db)


@router.get("/{task_id}", response_model=TaskOut)
def get_task(
    task_id: int,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None),
):
    """取单个任务详情（含 channels，供任务详情页通知渠道配置用）。"""
    task = _get_owned(task_id, user, x_device_id, db)
    return _task_out(task, db)


@router.post("/{task_id}/renew", response_model=TaskOut)
def renew_task(
    task_id: int,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None),
):
    """一键续期：expires_at = max(now, 原 expires_at) + 30 天（断裂-7）。

    R4-P2：提前续期不再丢剩余天数（此前一律 now+30d，剩 20 天时续期反而亏）。
    只延长时间，不改 paused/配额状态。匿名 trial 任务同样受 24h 上限钳制。
    """
    task = _get_owned(task_id, user, x_device_id, db)
    now = datetime.utcnow()
    base = max(now, task.expires_at) if task.expires_at else now
    task.expires_at = _clamp_expires(base + timedelta(days=30), anonymous=user is None)
    db.add(task)
    db.commit()
    db.refresh(task)
    log.info("task_renewed", task_id=task_id, expires_at=task.expires_at)
    return _task_out(task, db)


@router.delete("/{task_id}", status_code=204)
def delete_task(
    task_id: int,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None),
):
    task = _get_owned(task_id, user, x_device_id, db)
    # R5-B-2：db.py 未设 PRAGMA foreign_keys=ON，SQLite 层 FK 的 ondelete="SET NULL"
    # 不会触发——删任务前显式把关联通知的 task_id 置 NULL（通知靠 part_number
    # 快照列保留型号信息，见 B-N2），否则通知悬空、历史 join 丢数据。
    # （StockState 有 ORM cascade="all,delete" 兜底，无需显式处理。）
    db.execute(update(Notification).where(Notification.task_id == task.id).values(task_id=None))
    db.delete(task)
    db.commit()
    log.info("task_deleted", task_id=task_id)
    return None


@router.get("/{task_id}/states")
def task_states(
    task_id: int,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None),
):
    """按门店 × 配置的状态列表（六态 + 原始字段）。"""
    task = _get_owned(task_id, user, x_device_id, db)
    rows = db.execute(select(StockState).where(StockState.task_id == task.id)).scalars().all()
    return [
        {
            "store_number": r.store_number,
            "part_number": r.part_number,
            "state": _display_state(task, r),
            "pickup_display": r.pickup_display,
            "store_pick_eligible": r.store_pick_eligible,
            "pickup_search_quote": r.pickup_search_quote,
            "confirmed_count": r.confirmed_count,
            "last_event_at": r.last_event_at.isoformat() + "Z" if r.last_event_at else None,
            "updated_at": r.updated_at.isoformat() + "Z" if r.updated_at else None,
        }
        for r in rows
    ]
