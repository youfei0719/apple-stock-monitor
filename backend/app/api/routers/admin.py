"""后台（/api/admin/*）：需 admin 会话 + TOTP；所有写操作记 audit log。"""

import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import desc, func, select, text
from sqlalchemy.orm import Session

from app.api.deps import _client_ip, get_current_admin
from app.api.errors import APIError
from app.api.routers.pay import apply_tier_grant
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
from app.services.engine import PEAK_MODE_KEY, get_config, read_engine_status
from app.services.lifecycle import converge_task_limit

router = APIRouter(prefix="/admin", tags=["admin"])
log = get_logger("admin")


class AdminUserPatchEx(AdminUserPatchIn):
    """AdminUserPatchIn 的扩展：补单时可填 tier_expires_at（断裂-17）。

    注：schemas.py 归 Worker B 维护，此处用子类扩展避免改动其文件；
    契约上是 AdminUserPatchIn 的超集（新增可选字段 tier_expires_at）。
    """

    tier_expires_at: datetime | None = None
    # D5b：运营兜底——SMTP 故障导致用户收不到验证码时，管理员手动标记邮箱已验证
    email_verified: bool | None = None


class PaymentClaimIn(BaseModel):
    user_id: int


class PeakModeIn(BaseModel):
    enabled: bool


class RefundIn(BaseModel):
    """退款二次确认：用户还有其他有效 paid 订单时，必须显式 force=true 才降档。"""

    force: bool = False


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
    # R4-P2：today_pushes 按北京时间口径统计（此前按 UTC，用户看到的"今日"少 8 小时）。
    beijing_today = (datetime.utcnow() + timedelta(hours=8)).date()
    today_pushes = db.execute(
        select(func.count())
        .select_from(Notification)
        .where(Notification.kind == "stock_alert")
        .where(Notification.status == "sent")
        .where(func.date(Notification.created_at, "+8 hours") == beijing_today)
    ).scalar()
    revenue = db.execute(
        select(func.coalesce(func.sum(Payment.amount_cny), 0)).where(Payment.status == "paid")
    ).scalar()
    active_tasks = db.execute(
        select(func.count()).select_from(MonitorTask).where(MonitorTask.paused.is_(False))
    ).scalar()
    # 待处理支付（断裂-15/16）：未认领订单 + 金额异常 + 未知 plan（R4-P1-D5）
    unclaimed = db.execute(
        select(func.count())
        .select_from(Payment)
        .where(Payment.user_id.is_(None))
        .where(Payment.status.in_(["paid", "amount_mismatch", "unknown_plan"]))
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
            "email_verified": u.email_verified,
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
        if data.tier == "free":
            # R4-P1-D2：手动降回 free 时同步清空 tier_expires_at/pending_tier，
            # 与 /payments/{id}/refund 语义一致（否则用户再买时 apply_tier_grant
            # 会取未来值白送天数）。
            changes["tier_expires_at"] = (
                target.tier_expires_at.isoformat() if target.tier_expires_at else None,
                None,
            )
            changes["pending_tier"] = (target.pending_tier, None)
            target.tier_expires_at = None
            target.pending_tier = None
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
    if data.email_verified is not None:
        # D5b：管理员手动验邮（SMTP 故障兜底；audit 留痕）
        changes["email_verified"] = (target.email_verified, data.email_verified)
        target.email_verified = data.email_verified
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


# 金额异常待处理计数 key（与 pay.py 的 AMOUNT_MISMATCH_PENDING_KEY 同源）
_AMOUNT_MISMATCH_PENDING_KEY = "payments_amount_mismatch_pending"


def _dec_amount_mismatch_pending(db: Session) -> int:
    """金额异常待处理数 -1（下限 0），返回最新值。认领/关闭/退款动作消费队列时调用。

    R5-竞态-2 配套：与 pay._bump_amount_mismatch_pending 一样走单条
    INSERT...ON CONFLICT...UPDATE 原子语句（MAX(...,0) 保下限），
    无 read-modify-write，RETURNING 取回最新值。
    """
    row = db.execute(
        text(
            "INSERT INTO system_config (key, value, updated_at) "
            'VALUES (:key, \'{"count": 0}\', :now) '
            "ON CONFLICT(key) DO UPDATE SET "
            "value = json_set(system_config.value, '$.count', "
            "MAX(COALESCE(json_extract(system_config.value, '$.count'), 0) - 1, 0)), "
            "updated_at = excluded.updated_at "
            "RETURNING json_extract(value, '$.count') AS count"
        ),
        {"key": _AMOUNT_MISMATCH_PENDING_KEY, "now": datetime.utcnow()},
    ).first()
    return int(row[0]) if row else 0


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
        default=None,
        description="按状态筛选：paid/refunded/cancelled/amount_mismatch/unknown_plan/resolved",
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
            Payment.status.in_(["paid", "amount_mismatch", "unknown_plan"])
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
    old_tier = (user.tier, user.tier_expires_at.isoformat() if user.tier_expires_at else None)
    # R4-P1-D3：复用 webhook 的 apply_tier_grant（升级立即生效 / 降级到期生效），
    # 不再内联直接 user.tier = tier_to（此前管理员认领 standard 给在效期 pro
    # 用户会立即降级，与 webhook 口径矛盾）。
    grant_result = apply_tier_grant(db, user, tier_to)
    db.add(user)
    # 补单联动回填 Payment.user_id（断裂-17）
    p.user_id = user.id
    # D4：认领成功后 amount_mismatch 从待处理队列移除（status=resolved），
    # 待处理计数同步递减
    pending_count = None
    if p.status == "amount_mismatch":
        p.status = "resolved"
        pending_count = _dec_amount_mismatch_pending(db)
    db.add(p)
    detail = {
        "user_id": user.id,
        "tier": {"from": old_tier[0], "to": tier_to},
        "grant_result": grant_result,
        "tier_expires_at": user.tier_expires_at.isoformat() if user.tier_expires_at else None,
        "payment_status": p.status,
    }
    audit(db, admin, "payment.claim", "payment", payment_id, detail, _client_ip(request))
    db.commit()
    log.info("payment_claimed", payment_id=payment_id, user_id=user.id, tier=tier_to)
    out = {
        "ok": True,
        "payment_id": p.id,
        "user_id": user.id,
        "tier": user.tier,
        "pending_tier": user.pending_tier,
        "tier_expires_at": user.tier_expires_at.isoformat() + "Z" if user.tier_expires_at else None,
        "payment_status": p.status,
    }
    if pending_count is not None:
        out["pending_count"] = pending_count
    return out


@router.post("/payments/{payment_id}/close")
def close_payment(
    payment_id: int,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """D4：不予开通直接关闭——金额异常/未知 plan 的待处理订单人工定夺为"不处理"时，
    status 置 resolved（从待处理队列移除），不绑定用户、不开通档位。

    R5-B-4 选择说明（选了"拒绝已开通订单"这条路）：已开通（status=paid）的订单
    不接受 close——close 不做任何档位回退/资金回滚，若放行会留下"钱货两清但账上
    仍是 paid"的幽灵状态；已开通订单必须走 POST /payments/{id}/refund（降档 +
    配额重算 + 任务收敛 + 审计），口径唯一、可追溯。
    """
    p = db.get(Payment, payment_id)
    if not p:
        raise APIError(404, "订单不存在", "not_found")
    # R5-B-4：已开通的 paid 订单拒绝关闭，提示走退款流程
    if p.status == "paid":
        raise APIError(400, "订单已开通，请走退款流程（POST /payments/{id}/refund）", "use_refund_flow")
    if p.status not in ("amount_mismatch", "unknown_plan"):
        raise APIError(400, f"订单状态 {p.status} 不可关闭", "bad_status")
    old_status = p.status
    p.status = "resolved"
    db.add(p)
    # R5-B-1：unknown_plan 与 amount_mismatch 共用同一待处理计数（webhook 落库时
    # 两者都会 _bump），关闭任一都要递减，否则计数永远涨不回去
    pending_count = _dec_amount_mismatch_pending(db)
    audit(
        db,
        admin,
        "payment.close",
        "payment",
        payment_id,
        {"from": old_status, "to": "resolved"},
        _client_ip(request),
    )
    db.commit()
    log.info("payment_closed", payment_id=payment_id, from_status=old_status)
    out = {"ok": True, "payment_id": p.id, "payment_status": p.status}
    if pending_count is not None:
        out["pending_count"] = pending_count
    return out


@router.post("/payments/{payment_id}/refund")
def refund_payment(
    payment_id: int,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
    body: RefundIn | None = None,
):
    """D3：标记退款——联动：
    - payment.status='refunded'（revenue 统计只计 paid，已自动排除）
    - 清空该用户 tier_expires_at 并降回 free（pending_tier 同步清空）
    - R4-P1-B7：quota_reset_at 重置为 now+30 天（退款后用户按免费档重新起算周期）
    - R4-P1-B8：立即收敛任务数（复用 sweep keep_limit 口径），别让 pro 的
      30 个任务继续吃 free 配额
    - R4-P0-3：amount_mismatch → refunded 时待处理计数递减

    R5-B-3：退款前查用户是否还有其他 status='paid' 的有效订单——有则说明当前
    档位可能来自另一笔订单，无条件打回 free 会误杀。该场景下 400 拒绝
    （code=has_active_paid_orders），前端据此弹二次确认；确认后传 force=true
    再调一次才会真正降档。
    """
    p = db.get(Payment, payment_id)
    if not p:
        raise APIError(404, "订单不存在", "not_found")
    if p.status == "refunded":
        raise APIError(400, "订单已标记退款", "already_refunded")
    old_status = p.status
    # R5-B-1 配套：unknown_plan 与 amount_mismatch 共用待处理计数，
    # 标记退款同样消费队列（claim/close/refund 三条终态路径都要递减）
    pending_count = None
    if old_status in ("amount_mismatch", "unknown_plan"):
        pending_count = _dec_amount_mismatch_pending(db)
    p.status = "refunded"
    db.add(p)
    user_info = None
    if p.user_id is not None:
        user = db.get(User, p.user_id)
        if user:
            other_paid = db.execute(
                select(func.count())
                .select_from(Payment)
                .where(
                    Payment.user_id == user.id,
                    Payment.id != p.id,
                    Payment.status == "paid",
                )
            ).scalar()
            # R5-B-3：还有其他有效已开通订单 → 要求二次确认，不无条件打回 free
            if other_paid and not (body and body.force):
                db.rollback()
                raise APIError(
                    400,
                    f"用户还有 {other_paid} 笔有效已开通订单，当前档位可能来自其他订单；"
                    "确认继续降档请传 force=true",
                    "has_active_paid_orders",
                )
            user.tier = "free"
            user.tier_expires_at = None
            user.pending_tier = None
            # R4-P1-B7：退款后配额锚点按免费档重算——从退款时刻起新的 30 天周期
            user.quota_reset_at = datetime.utcnow() + timedelta(days=30)
            db.add(user)
            # R4-P1-B8：tier 已是 free，membership_sweep 会跳过，pro 的 30 个任务
            # 会继续轮询；这里立即执行一次任务数收敛（free 上限）
            converged = converge_task_limit(db, user)
            user_info = {
                "user_id": user.id,
                "tier": {"from": old[0], "to": "free"},
                "quota_reset_at": user.quota_reset_at.isoformat() + "Z",
                "tasks_paused": len(converged),
            }
    audit(
        db,
        admin,
        "payment.refund",
        "payment",
        payment_id,
        {"from": old_status, "to": "refunded", **({"user": user_info} if user_info else {})},
        _client_ip(request),
    )
    db.commit()
    log.info(
        "payment_refunded",
        payment_id=payment_id,
        from_status=old_status,
        user_id=p.user_id,
    )
    out = {"ok": True, "payment_id": p.id, "payment_status": p.status, "user": user_info}
    if pending_count is not None:
        out["pending_count"] = pending_count
    return out


@router.get("/system")
def system_status(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    # D1：API 进程不跑引擎（内存状态恒 stopped），引擎状态读独立 engine 进程
    # 每 tick 写进 system_config 的心跳，形状与旧 engine.status() 对齐。
    cooldown = get_config(db, "apple_cooldown", {})
    log_tail = _tail_log()
    return {
        "engine": read_engine_status(db),
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
