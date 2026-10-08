"""后台（/api/admin/*）：需 admin 会话 + TOTP；所有写操作记 audit log。"""

import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.api.deps import _client_ip, get_current_admin
from app.api.errors import APIError
from app.core.db import get_db
from app.core.logging import get_logger
from app.core.tiers import VALID_TIERS
from app.models.models import ApiHit, AuditLog, MonitorTask, Notification, Payment, User
from app.schemas import AdminUserPatchIn
from app.services.engine import engine

router = APIRouter(prefix="/admin", tags=["admin"])
log = get_logger("admin")


def audit(
    db: Session,
    admin: User,
    action: str,
    target_type: str,
    target_id: str,
    detail: dict,
    ip: str,
):
    db.add(
        AuditLog(
            admin_id=admin.id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            detail=detail,
            ip=ip,
        )
    )


@router.get("/overview")
def overview(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    total_users = db.execute(select(func.count()).select_from(User)).scalar()
    tier_rows = db.execute(select(User.tier, func.count()).group_by(User.tier)).all()
    today = datetime.utcnow().date()
    today_pushes = db.execute(
        select(func.count())
        .select_from(Notification)
        .where(Notification.kind == "stock_alert")
        .where(Notification.status == "sent")
        .where(func.date(Notification.created_at) == today)
    ).scalar()
    revenue = db.execute(
        select(func.coalesce(func.sum(Payment.amount_cny), 0)).where(Payment.status == "paid")
    ).scalar()
    active_tasks = db.execute(
        select(func.count()).select_from(MonitorTask).where(MonitorTask.paused.is_(False))
    ).scalar()
    return {
        "total_users": total_users,
        "tier_distribution": {t: c for t, c in tier_rows},
        "today_pushes": today_pushes,
        "revenue_cny": float(revenue),
        "active_tasks": active_tasks,
    }


@router.get("/traffic")
def traffic(
    days: int = Query(default=30, ge=1, le=180),
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    since = datetime.utcnow() - timedelta(days=days)
    rows = db.execute(
        select(
            func.date(ApiHit.created_at).label("day"),
            func.count().label("pv"),
            func.count(func.distinct(ApiHit.ip_hash)).label("uv"),
        )
        .where(ApiHit.created_at >= since)
        .group_by(func.date(ApiHit.created_at))
        .order_by("day")
    ).all()
    return [{"day": str(r.day), "pv": r.pv, "uv": r.uv} for r in rows]


@router.get("/users")
def list_users(
    q: str | None = Query(default=None),
    tier: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    query = select(User).order_by(desc(User.created_at)).limit(limit)
    if q:
        query = query.where(User.email.like(f"%{q}%"))
    if tier:
        query = query.where(User.tier == tier)
    users = db.execute(query).scalars().all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "tier": u.tier,
            "tier_expires_at": u.tier_expires_at.isoformat() + "Z" if u.tier_expires_at else None,
            "is_admin": u.is_admin,
            "totp_enabled": u.totp_enabled,
            "created_at": u.created_at.isoformat() + "Z",
        }
        for u in users
    ]


@router.patch("/users/{user_id}")
def patch_user(
    user_id: int,
    data: AdminUserPatchIn,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if not target:
        raise APIError(404, "用户不存在", "not_found")
    changes = {}
    if data.tier is not None:
        if data.tier not in VALID_TIERS:
            raise APIError(400, "tier 非法", "bad_tier")
        changes["tier"] = (target.tier, data.tier)
        target.tier = data.tier
    if data.is_admin is not None:
        changes["is_admin"] = (target.is_admin, data.is_admin)
        target.is_admin = data.is_admin
    if data.paused_tasks:
        tasks = (
            db.execute(select(MonitorTask).where(MonitorTask.user_id == user_id)).scalars().all()
        )
        for t in tasks:
            t.paused = True
            db.add(t)
        changes["paused_tasks"] = len(tasks)
    db.add(target)
    audit(db, admin, "user.patch", "user", user_id, changes, _client_ip(request))
    db.commit()
    log.info("admin_user_patch", admin_id=admin.id, user_id=user_id, changes=changes)
    return {
        "ok": True,
        "changes": {
            k: ({"from": v[0], "to": v[1]} if isinstance(v, tuple) else v)
            for k, v in changes.items()
        },
    }


@router.get("/payments")
def list_payments(
    limit: int = Query(default=100, ge=1, le=500),
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    rows = (
        db.execute(select(Payment).order_by(desc(Payment.created_at)).limit(limit)).scalars().all()
    )
    return [
        {
            "id": p.id,
            "user_id": p.user_id,
            "order_id": p.order_id,
            "plan": p.plan,
            "amount_cny": p.amount_cny,
            "tier_from": p.tier_from,
            "tier_to": p.tier_to,
            "status": p.status,
            "created_at": p.created_at.isoformat() + "Z",
        }
        for p in rows
    ]


@router.get("/system")
def system_status(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    from app.services.engine import get_config

    cooldown = get_config(db, "apple_cooldown", {})
    log_tail = _tail_log()
    return {
        "engine": engine.status(),
        "apple_cooldown": cooldown,
        "log_tail": log_tail,
    }


def _tail_log(n: int = 100) -> list[str]:
    candidates = ["logs/app.log"]
    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    lines = f.readlines()
                return [ln.rstrip("\n") for ln in lines[-n:]]
            except OSError:
                return []
    return []


@router.get("/audit")
def list_audit(
    limit: int = Query(default=100, ge=1, le=500),
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    rows = (
        db.execute(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit))
        .scalars()
        .all()
    )
    return [
        {
            "id": a.id,
            "admin_id": a.admin_id,
            "action": a.action,
            "target_type": a.target_type,
            "target_id": a.target_id,
            "detail": a.detail,
            "ip": a.ip,
            "created_at": a.created_at.isoformat() + "Z",
        }
        for a in rows
    ]
