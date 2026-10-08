"""监控任务 CRUD + 批量生成 + 状态查询。

匿名体验：无会话时可用 X-Device-Id 创建 1 个任务（不计配额）。
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import _client_ip, get_current_user, get_optional_user
from app.api.errors import APIError
from app.core.db import get_db
from app.core.logging import get_logger
from app.core.ratelimit import check_rate_limit
from app.core.tiers import effective_tier_of, tier_of
from app.models.models import MonitorTask, StockState, User
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

# auto_retire DB 列是否已存在（models.py 归属其他 worker，加列需 migration；
# 列缺失时开关按默认值 True 生效，显式关闭会报 501，见下）。
HAS_AUTO_RETIRE_COL = hasattr(MonitorTask, "auto_retire")

# 匿名体验任务最长存活 24h（服务端强制，不信任客户端传的 expires_at）
TRIAL_MAX_TTL = timedelta(hours=24)
# 任务创建接口 IP 维度限流：20 次/小时/IP
TASK_CREATE_LIMIT = 20
TASK_CREATE_WINDOW_SEC = 3600


def _clamp_expires(expires_at: datetime | None, anonymous: bool) -> datetime | None:
    """匿名任务强制 24h 过期：为空或超过 24h 时一律 clamp 到 now+24h。"""
    if not anonymous:
        return expires_at
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
    rows = db.execute(select(StockState).where(StockState.task_id == task.id)).scalars().all()
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
    """断裂-8：按 (归属, part_number, 门店集合) 查重，避免重复任务重复通知/扣配额。"""
    q = select(MonitorTask)
    if user_id is not None:
        q = q.where(MonitorTask.user_id == user_id)
    else:
        q = q.where(MonitorTask.device_id == device_id, MonitorTask.user_id.is_(None))
    want = frozenset(store_numbers)
    for t in db.execute(q).scalars().all():
        if t.part_number == part_number and frozenset(t.store_numbers or []) == want:
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


@router.get("", response_model=list[TaskOut])
def list_tasks(
    status: str = Query(default="all", description="active=监控中, expired=已过期, all=全部"),
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None),
):
    if status not in ("active", "expired", "all"):
        raise APIError(400, "status 必须为 active、expired 或 all", "bad_status")
    q = select(MonitorTask).order_by(MonitorTask.created_at.desc())
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
    _validate_task_in(data)
    _check_task_limit(db, user, x_device_id)
    stores = [s.model_dump() for s in data.stores]
    part_number = data.part_number.strip().upper()
    store_numbers = [s["number"] for s in stores]
    # 断裂-8：重复任务冲突检测
    conflict = _find_conflict(
        db, user.id if user else None, x_device_id, part_number, store_numbers
    )
    if conflict:
        raise _conflict_error(conflict)
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
        channels=data.channels.model_dump(exclude_none=True),
        expires_at=_clamp_expires(data.expires_at, anonymous=user is None),
    )
    if HAS_AUTO_RETIRE_COL:
        task_kwargs["auto_retire"] = data.auto_retire
    elif data.auto_retire is not True:
        # 列尚未建：只能接受默认值 True，显式关闭必须等 DB 迁移
        raise APIError(
            501, "auto_retire 开关后端尚未启用（需 DB 迁移），暂只支持默认开启", "not_implemented"
        )
    task = MonitorTask(**task_kwargs)
    db.add(task)
    db.commit()
    db.refresh(task)
    log.info("task_created", task_id=task.id, user_id=user.id if user else None)
    return _task_out(task, db)


@router.post("/batch", response_model=list[TaskOut], status_code=201)
def batch_create(
    data: TaskBatchIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """门店 × 型号批量生成任务。"""
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
    if data.mode not in ("instant", "confirmed"):
        raise APIError(400, "mode 必须为 instant 或 confirmed", "bad_mode")
    # 断裂-8：批量内去重 + 与已有任务查重（409）
    seen: set[tuple[str, frozenset]] = set()
    for part, store in combos:
        pn = part.strip().upper()
        key = (pn, frozenset([store]))
        if key in seen:
            raise APIError(409, f"批量内重复：{pn} × {store} 出现了多次", "task_conflict")
        seen.add(key)
        conflict = _find_conflict(db, user.id, None, pn, [store])
        if conflict:
            raise _conflict_error(conflict)
    channels = data.channels.model_dump(exclude_none=True)
    created = []
    for part, store in combos:
        name = data.name_template.replace("{part_number}", part).replace("{store_number}", store)
        task = MonitorTask(
            user_id=user.id,
            name=name,
            category=data.category,
            part_number=part.strip().upper(),
            store_numbers=[store],
            stores=[{"number": store, "name": "", "city": ""}],
            mode=data.mode,
            channels=channels,
        )
        db.add(task)
        created.append(task)
    db.commit()
    for t in created:
        db.refresh(t)
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
    fields = ("name", "group", "paused", "mode", "repeat_interval_sec")
    for field in fields:
        v = getattr(data, field)
        if v is not None:
            setattr(task, field, v)
    if data.channels is not None:
        task.channels = data.channels.model_dump(exclude_none=True)
    if data.auto_retire is not None:
        if HAS_AUTO_RETIRE_COL:
            task.auto_retire = data.auto_retire
        else:
            raise APIError(501, "auto_retire 开关后端尚未启用（需 DB 迁移）", "not_implemented")
    if data.expires_at is not None:
        # 匿名任务同样强制 24h 上限
        task.expires_at = _clamp_expires(data.expires_at, anonymous=user is None)
    if data.mode is not None and data.mode not in ("instant", "confirmed"):
        raise APIError(400, "mode 必须为 instant 或 confirmed", "bad_mode")
    db.add(task)
    db.commit()
    db.refresh(task)
    return _task_out(task, db)


@router.post("/{task_id}/renew", response_model=TaskOut)
def renew_task(
    task_id: int,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None),
):
    """一键续期：expires_at = now + 30 天（断裂-7）。

    只延长时间，不改 paused/配额状态。匿名 trial 任务同样受 24h 上限钳制。
    """
    task = _get_owned(task_id, user, x_device_id, db)
    task.expires_at = _clamp_expires(datetime.utcnow() + timedelta(days=30), anonymous=user is None)
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
