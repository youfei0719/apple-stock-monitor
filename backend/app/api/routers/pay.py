"""支付（爱发电）：回调自动开通/续期会员、付费记录查询。

签名校验：HMAC-SHA256(raw_body, key=AFDIAN_TOKEN) 的 hex 与请求头
X-Afdian-Signature 比对（头名经社区 afdianbot 用法确认）。

重要：该算法【未与爱发电官方文档核对】（官方 Webhook 签名文档未公开可查），
【联调前勿用】。上线前必须用爱发电后台的真实回调做一次签名对拍，
确认算法一致后再启用。若对拍不符，按官方文档修正本函数。

验签为 fail-closed：AFDIAN_TOKEN 为空、签名缺失或不符一律拒绝。

对账兜底：webhook 丢失 / 金额异常 / 退款无承接 → 设计见
docs/reviews/reconciliation-plan.md（首版只留文档，未实现）。
"""

import hashlib
import hmac
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import desc, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.errors import APIError
from app.core.config import get_settings
from app.core.db import get_db
from app.core.logging import get_logger
from app.core.tiers import effective_tier
from app.models.models import Payment, User

router = APIRouter(tags=["pay"])
log = get_logger("pay")
settings = get_settings()

# 档位期望金额（单位：分）。standard=¥19，pro=¥39。
EXPECTED_AMOUNT_FEN = {"standard": 1900, "pro": 3900}

# 档位高低：用于判断 webhook 是升级还是降级（升级立即生效、降级到期生效）
TIER_RANK = {"trial": 0, "free": 1, "standard": 2, "pro": 3}

# 金额异常待处理计数 key（admin 待处理视图展示，断裂-15）
AMOUNT_MISMATCH_PENDING_KEY = "payments_amount_mismatch_pending"


def _resolve_user(db: Session, remark: str, user_id_raw: str) -> User | None:
    """关联用户：优先 remark 中填写的 user_id / email，再看回调自带 user_id。"""
    user: User | None = None
    if remark.isdigit():
        user = db.get(User, int(remark))
    if not user and "@" in remark:
        user = db.execute(select(User).where(User.email == remark)).scalar_one_or_none()
    if not user and str(user_id_raw).isdigit():
        user = db.get(User, int(str(user_id_raw)))
    return user


def _bump_amount_mismatch_pending(db: Session) -> int:
    """金额异常待处理数 +1，返回最新值。

    R5-竞态-2：改原子递增——单条 INSERT...ON CONFLICT...UPDATE，无
    read-modify-write（并发 webhook 同时落库 amount_mismatch/unknown_plan 时
    计数不再丢）。RETURNING 取回递增后的最新值。
    """
    row = db.execute(
        text(
            "INSERT INTO system_config (key, value, updated_at) "
            'VALUES (:key, \'{"count": 1}\', :now) '
            "ON CONFLICT(key) DO UPDATE SET "
            "value = json_set(system_config.value, '$.count', "
            "COALESCE(json_extract(system_config.value, '$.count'), 0) + 1), "
            "updated_at = excluded.updated_at "
            "RETURNING json_extract(value, '$.count') AS count"
        ),
        {"key": AMOUNT_MISMATCH_PENDING_KEY, "now": datetime.utcnow()},
    ).first()
    return int(row[0]) if row else 0


def apply_tier_grant(db: Session, user: User, tier_to: str) -> str:
    """按 webhook/补单口径授予档位（可单独单元测试）。

    返回 "granted"（升级/续费立即生效）或 "downgrade_pending"（降级到期生效，
    只写 pending_tier，到期 sweep 再切换；不自洽-1）。
    配额锚点与会员周期对齐（购买日+30天滚动）。
    """
    now = datetime.utcnow()
    base = user.tier_expires_at if user.tier_expires_at and user.tier_expires_at > now else now
    if TIER_RANK.get(tier_to, 0) < TIER_RANK.get(effective_tier(user), 0):
        user.pending_tier = tier_to
        db.add(user)
        log.info(
            "tier_downgrade_pending",
            user_id=user.id,
            tier_from=effective_tier(user),
            pending_tier=tier_to,
        )
        return "downgrade_pending"
    user.tier = tier_to
    user.tier_expires_at = base + timedelta(days=30)
    user.quota_reset_at = base + timedelta(days=30)
    user.pending_tier = None
    db.add(user)
    log.info(
        "tier_granted",
        user_id=user.id,
        tier_to=tier_to,
        tier_expires_at=user.tier_expires_at.isoformat(),
    )
    return "granted"


def _add_payment_atomic(db: Session, payment: Payment) -> Payment | None:
    """幂等原子落库（R4-P2）。

    重复回调先查 select 再插 insert 的竞态窗口里，并发第二个请求会撞
    order_id 唯一约束：捕获 IntegrityError → 回滚 → 返回 None，
    调用方按 duplicate 处理，不再 500。
    """
    db.add(payment)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        log.warning("afdian_payment_race_duplicate", order_id=payment.order_id)
        return None
    return payment


def verify_afdian_signature(raw_body: bytes, signature: str | None) -> bool:
    """验签（fail-closed）：无 token / 无签名 / 不符一律返回 False。"""
    token = (settings.AFDIAN_TOKEN or "").strip()
    if not token:
        # fail-closed：没有配置 token 时直接拒绝，绝不放行
        log.error("afdian_signature_rejected_no_token")
        return False
    if not signature:
        return False
    digest = hmac.new(token.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature.strip())


def _tier_by_plan() -> dict[str, str]:
    """plan_id -> tier 映射（延迟构建；plan_id 为空时不参与映射）。"""
    m: dict[str, str] = {}
    if settings.AFDIAN_PLAN_STANDARD:
        m[settings.AFDIAN_PLAN_STANDARD] = "standard"
    if settings.AFDIAN_PLAN_PRO:
        m[settings.AFDIAN_PLAN_PRO] = "pro"
    return m


@router.post("/pay/afdian-webhook")
async def afdian_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    signature = request.headers.get("x-afdian-signature")
    if not verify_afdian_signature(raw, signature):
        log.warning("afdian_bad_signature")
        raise APIError(403, "签名校验失败", "bad_signature")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise APIError(400, "payload 不是合法 JSON", "bad_payload") from e

    data = payload.get("data") or {}
    order = data.get("order") or {}
    order_id = str(order.get("out_trade_no") or data.get("out_trade_no") or "")
    plan_id = str(order.get("plan_id") or data.get("plan_id") or "")
    user_id_raw = order.get("user_id") or data.get("user_id") or ""
    remark = str(order.get("remark") or data.get("remark") or "")
    # total_amount 单位为分
    amount_fen = int(float(order.get("total_amount") or data.get("total_amount") or 0))

    if not order_id:
        raise APIError(400, "缺少订单号", "bad_order")

    # 幂等：重复回调直接返回
    dup = db.execute(select(Payment).where(Payment.order_id == order_id)).scalar_one_or_none()
    if dup:
        return {"ok": True, "duplicate": True}

    tier_to = _tier_by_plan().get(plan_id)
    if not tier_to:
        # R4-P1-D5：未知 plan_id 不再直接 400（爱发电会重试，永远 400 成黑洞，
        # 已扣款订单在 Payment 表和后台都看不见）。落库 status='unknown_plan'
        # 返回 200，计入待处理（admin 待处理视图可见、可关闭）。
        log.warning("afdian_unknown_plan", plan_id=plan_id, order_id=order_id)
        user = _resolve_user(db, remark, user_id_raw)
        row = _add_payment_atomic(
            db,
            Payment(
                user_id=user.id if user else None,
                order_id=order_id,
                plan=plan_id,
                amount_cny=amount_fen / 100,
                tier_from=effective_tier(user) if user else "",
                tier_to="",
                status="unknown_plan",
                raw_payload=payload,
            ),
        )
        if row is None:
            return {"ok": True, "duplicate": True}
        pending = _bump_amount_mismatch_pending(db)
        db.commit()
        log.error(
            "afdian_unknown_plan_recorded",
            order_id=order_id,
            user_id=user.id if user else None,
            pending_count=pending,
        )
        return {
            "ok": True,
            "status": "unknown_plan",
            "user_id": user.id if user else None,
            "pending_count": pending,
            "detail": "未知 plan_id，已记录待人工处理（admin 待处理视图）",
        }

    # 金额与档位价比对：不符不再直接 400 拒绝（断裂-15）。
    # 爱发电已扣款（不退），拒绝会导致"钱货两空"黑洞；改为落库待人工处理。
    expected_fen = EXPECTED_AMOUNT_FEN[tier_to]
    if amount_fen != expected_fen:
        log.error(
            "afdian_amount_mismatch",
            order_id=order_id,
            plan_id=plan_id,
            tier=tier_to,
            amount_fen=amount_fen,
            expected_fen=expected_fen,
        )
        user = _resolve_user(db, remark, user_id_raw)
        row = _add_payment_atomic(
            db,
            Payment(
                user_id=user.id if user else None,
                order_id=order_id,
                plan=plan_id,
                amount_cny=amount_fen / 100,
                tier_from=effective_tier(user) if user else "",
                tier_to=tier_to,
                status="amount_mismatch",
                raw_payload=payload,
            ),
        )
        if row is None:
            return {"ok": True, "duplicate": True}
        pending = _bump_amount_mismatch_pending(db)
        db.commit()
        log.error(
            "afdian_amount_mismatch_recorded",
            order_id=order_id,
            user_id=user.id if user else None,
            pending_count=pending,
        )
        return {
            "ok": True,
            "status": "amount_mismatch",
            "user_id": user.id if user else None,
            "pending_count": pending,
            "detail": "实付金额与档位不符，已记录待人工处理（admin 待处理视图）",
        }

    # 关联用户：优先 remark 中填写的 user_id / email
    user = _resolve_user(db, remark, user_id_raw)

    tier_from = effective_tier(user) if user else ""
    if user:
        result = apply_tier_grant(db, user, tier_to)
        if result == "downgrade_pending":
            log.info(
                "afdian_downgrade_pending",
                order_id=order_id,
                user_id=user.id,
                tier_from=tier_from,
                pending_tier=tier_to,
            )

    row = _add_payment_atomic(
        db,
        Payment(
            user_id=user.id if user else None,
            order_id=order_id,
            plan=plan_id,
            amount_cny=amount_fen / 100,
            tier_from=tier_from,
            tier_to=tier_to,
            status="paid",
            raw_payload=payload,
        ),
    )
    if row is None:
        return {"ok": True, "duplicate": True}
    db.commit()
    log.info("afdian_paid", order_id=order_id, tier_to=tier_to, user_id=user.id if user else None)
    return {"ok": True, "tier": tier_to, "user_id": user.id if user else None}


@router.get("/payments")
def my_payments(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (
        db.execute(
            select(Payment).where(Payment.user_id == user.id).order_by(desc(Payment.created_at))
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": p.id,
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
