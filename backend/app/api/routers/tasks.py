"""监控任务 CRUD + 批量生成 + 状态查询。

匿名体验：无会话时可用 X-Device-Id 创建 1 个任务（不计配额）。
"""

from fastapi import APIRouter, Depends, Header
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_optional_user
from app.api.errors import APIError
from app.core.db import get_db
from app.core.logging import get_logger
from app.core.tiers import tier_of
from app.models.models import MonitorTask, StockState, User
from app.schemas import TaskBatchIn, TaskCreateIn, TaskOut, TaskPatchIn

router = APIRouter(prefix="/tasks", tags=["tasks"])
log = get_logger("tasks")

DISPLAY_STATE_ORDER = ("available", "unavailable", "unknown", "verifying", "cooling", "paused")


def _display_state(task: MonitorTask, row: StockState | None) -> str:
    if task.paused:
        return "paused"
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
        created_at=task.created_at,
        latest={"stores": by_store, "available_count": available_n, "total": len(rows)},
    )


def _check_task_limit(db: Session, user: User | None, device_id: str | None) -> None:
    if user:
        tier = tier_of(user.tier)
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


def _validate_task_in(data: TaskCreateIn) -> None:
    if data.mode not in ("instant", "confirmed"):
        raise APIError(400, "mode 必须为 instant 或 confirmed", "bad_mode")
    if data.category not in ("iphone", "ipad", "mac", "watch"):
        raise APIError(400, "category 非法", "bad_category")


@router.get("", response_model=list[TaskOut])
def list_tasks(
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None),
):
    q = select(MonitorTask).order_by(MonitorTask.created_at.desc())
    if user:
        q = q.where(MonitorTask.user_id == user.id)
    elif x_device_id:
        q = q.where(MonitorTask.device_id == x_device_id)
    else:
        return []
    return [_task_out(t, db) for t in db.execute(q).scalars().all()]


@router.post("", response_model=TaskOut, status_code=201)
def create_task(
    data: TaskCreateIn,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None),
):
    if not user and not x_device_id:
        raise APIError(401, "匿名创建任务需要 X-Device-Id 头", "device_required")
    _validate_task_in(data)
    _check_task_limit(db, user, x_device_id)
    stores = [s.model_dump() for s in data.stores]
    task = MonitorTask(
        user_id=user.id if user else None,
        device_id=None if user else x_device_id,
        name=data.name,
        group=data.group,
        category=data.category,
        part_number=data.part_number.strip().upper(),
        product_name=data.product_name,
        color=data.color,
        capacity=data.capacity,
        store_numbers=[s["number"] for s in stores],
        stores=stores,
        mode=data.mode,
        repeat_interval_sec=data.repeat_interval_sec,
        channels=data.channels,
        expires_at=data.expires_at,
    )
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
    tier = tier_of(user.tier)
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
            channels=data.channels,
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
    fields = ("name", "group", "paused", "expires_at", "channels", "mode", "repeat_interval_sec")
    for field in fields:
        v = getattr(data, field)
        if v is not None:
            setattr(task, field, v)
    if data.mode is not None and data.mode not in ("instant", "confirmed"):
        raise APIError(400, "mode 必须为 instant 或 confirmed", "bad_mode")
    db.add(task)
    db.commit()
    db.refresh(task)
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
