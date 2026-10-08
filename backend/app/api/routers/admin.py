"""后台（/api/admin/*）：需 admin 会话 + TOTP；所有写操作记 audit log。"""

import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.api.deps import _client_ip, get_current_admin
from app.api.errors import APIError
from app.core.db import get_db
from app.core.logging import get_logger
from app.core.tiers import VALID_TIERS
from app.models.models import (
    ApiHit,
    AuditLog,
    MonitorTask,
    Notification,
    Payment,
    SystemConfig,
    User,
)
from app.schemas import AdminUserPatchIn
from app.services.engine import PEAK_MODE_KEY, engine, get_config

router = APIRouter(prefix="/admin", tags=["admin"])
log = get_logger("admin")


class AdminUserPatchEx(AdminUserPatchIn):
    """AdminUserPatchIn 的扩展：补单时可填 tier_expires_at（断裂-17）。

    注：schemas.py 归 Worker B 维护，此处用子类扩展避免改动其文件；
    契约上是 AdminUserPatchIn 的超集（新增可选字段 tier_expires_at）。
    """

    tier_expires_at: datetime | None = None


class PaymentClaimIn(BaseModel):
    user_id: int


class PeakModeIn(BaseModel):
    enabled: bool


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
    # 待处理支付（断裂-15/16）：未认领订单 + 金额异常
    unclaimed = db.execute(
        select(func.count())
        .select_from(Payment)
        .where(Payment.user_id.is_(None))
        .where(Payment.status.in_(["paid", "amount_mismatch"]))
    ).scalar()
    amount_mismatch = db.execute(
        select(func.count())
        .select_from(Payment)
        .where(Payment.status == "amount_mismatch")
    ).scalar()
    return {
        "total_users": total_users,
        "tier_distribution": {t: c for t, c in tier_rows},
        "today_pushes": today_pushes,
        "revenue_cny": float(revenue),
        "active_tasks": active_tasks,
        "pending_payments": {"unclaimed": unclaimed, "amount_mismatch": amount_mismatch},
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
    data: AdminUserPatchEx,
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
    if data.tier_expires_at is not None:
        # 补单入口：手动设定会员到期时间（断裂-17）。传 null 清空暂不支持，
        # 需要清空请走 DB（避免误操作把付费用户变成永久会员）。
        changes["tier_expires_at"] = (
            target.tier_expires_at.isoformat() if target.tier_expires_at else None,
            data.tier_expires_at.isoformat(),
        )
        target.tier_expires_at = data.tier_expires_at
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


def _payment_remark(p: Payment) -> str:
    """从 raw_payload 提取 remark（断裂-16：后台可识别未认领订单归属）。"""
    raw = p.raw_payload or {}
    data = raw.get("data") or {}
    order = data.get("order") or {}
    for src in (order, data, raw):
        r = src.get("remark")
        if r:
            return str(r)
    return ""


@router.get("/payments")
def list_payments(
    limit: int = Query(default=100, ge=1, le=500),
    status: str | None = Query(
        default=None, description="按状态筛选：paid/refunded/cancelled/amount_mismatch"
    ),
    claim_status: str | None = Query(
        default=None, description="unclaimed=待认领（user_id 为空）"
    ),
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    query = select(Payment).order_by(desc(Payment.created_at))
    if status:
        query = query.where(Payment.status == status)
    if claim_status == "unclaimed":
        query = query.where(Payment.user_id.is_(None)).where(
            Payment.status.in_(["paid", "amount_mismatch"])
        )
    rows = db.execute(query.limit(limit)).scalars().all()
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
            "remark": _payment_remark(p),
            "created_at": p.created_at.isoformat() + "Z",
        }
        for p in rows
    ]


@router.post("/payments/{payment_id}/claim")
def claim_payment(
    payment_id: int,
    data: PaymentClaimIn,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """认领未认领订单（断裂-16/17）：绑定用户 + 按订单档位开通 30 天 + 回填。

    退款反向操作：管理员手动把用户 tier 降回 free 时，走 PATCH /users/{id}
    传 {"tier": "free"} 即可（断裂-2）。
    """
    p = db.get(Payment, payment_id)
    if not p:
        raise APIError(404, "订单不存在", "not_found")
    if p.user_id is not None:
        raise APIError(400, "订单已被认领", "already_claimed")
    user = db.get(User, data.user_id)
    if not user:
        raise APIError(404, "用户不存在", "not_found")
    tier_to = p.tier_to
    if tier_to not in ("standard", "pro"):
        raise APIError(400, f"订单档位异常：{tier_to}", "bad_tier")
    now = datetime.utcnow()
    base = user.tier_expires_at if user.tier_expires_at and user.tier_expires_at > now else now
    old_tier = (user.tier, user.tier_expires_at.isoformat() if user.tier_expires_at else None)
    user.tier = tier_to
    user.tier_expires_at = base + timedelta(days=30)
    user.quota_reset_at = base + timedelta(days=30)
    user.pending_tier = None
    db.add(user)
    # 补单联动回填 Payment.user_id（断裂-17）；金额异常订单保持原状态由人工定夺
    p.user_id = user.id
    db.add(p)
    detail = {
        "user_id": user.id,
        "tier": {"from": old_tier[0], "to": tier_to},
        "tier_expires_at": user.tier_expires_at.isoformat(),
        "payment_status": p.status,
    }
    audit(db, admin, "payment.claim", "payment", payment_id, detail, _client_ip(request))
    db.commit()
    log.info("payment_claimed", payment_id=payment_id, user_id=user.id, tier=tier_to)
    return {
        "ok": True,
        "payment_id": p.id,
        "user_id": user.id,
        "tier": tier_to,
        "tier_expires_at": user.tier_expires_at.isoformat() + "Z",
        "payment_status": p.status,
    }


@router.get("/system")
def system_status(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    cooldown = get_config(db, "apple_cooldown", {})
    log_tail = _tail_log()
    return {
        "engine": engine.status(),
        "apple_cooldown": cooldown,
        "peak_mode": bool(get_config(db, PEAK_MODE_KEY, {}).get("enabled", False)),
        "log_tail": log_tail,
    }


@router.post("/system/peak-mode")
def set_peak_mode(
    data: PeakModeIn,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """高峰模式开关（对标-1）：开启后 trial/free 刷新间隔 ×4，记审计。"""
    row = db.execute(
        select(SystemConfig).where(SystemConfig.key == PEAK_MODE_KEY)
    ).scalar_one_or_none()
    if row:
        row.value = {"enabled": bool(data.enabled)}
        db.add(row)
    else:
        db.add(SystemConfig(key=PEAK_MODE_KEY, value={"enabled": bool(data.enabled)}))
    audit(
        db,
        admin,
        "system.peak_mode",
        "system",
        PEAK_MODE_KEY,
        {"enabled": bool(data.enabled)},
        _client_ip(request),
    )
    db.commit()
    log.info("peak_mode_set", enabled=bool(data.enabled), admin_id=admin.id)
    return {"ok": True, "peak_mode": bool(data.enabled)}


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
