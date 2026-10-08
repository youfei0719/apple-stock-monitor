"""支付（爱发电）：回调自动开通/续期会员、付费记录查询。

签名校验：HMAC-SHA256(raw_body, key=AFDIAN_TOKEN) 的 hex 与请求头
X-Afdian-Signature 比对（头名经社区 afdianbot 用法确认）。

重要：该算法【未与爱发电官方文档核对】（官方 Webhook 签名文档未公开可查），
【联调前勿用】。上线前必须用爱发电后台的真实回调做一次签名对拍，
确认算法一致后再启用。若对拍不符，按官方文档修正本函数。

验签为 fail-closed：AFDIAN_TOKEN 为空、签名缺失或不符一律拒绝。
"""

import hashlib
import hmac
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.errors import APIError
from app.core.config import get_settings
from app.core.db import get_db
from app.core.logging import get_logger
from app.models.models import Payment, User

router = APIRouter(tags=["pay"])
log = get_logger("pay")
settings = get_settings()

# 档位期望金额（单位：分）。standard=¥19，pro=¥39。
EXPECTED_AMOUNT_FEN = {"standard": 1900, "pro": 3900}


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
        log.warning("afdian_unknown_plan", plan_id=plan_id, order_id=order_id)
        raise APIError(400, f"未知 plan_id: {plan_id}", "unknown_plan")

    # 金额与档位价比对：不符说明回调异常或档位配置错误，直接拒绝并告警
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
        raise APIError(
            400,
            f"回调金额与档位不符（实付 {amount_fen} 分，档位应为 {expected_fen} 分）",
            "amount_mismatch",
        )

    # 关联用户：优先 remark 中填写的 user_id / email
    user: User | None = None
    if remark.isdigit():
        user = db.get(User, int(remark))
    if not user and "@" in remark:
        user = db.execute(select(User).where(User.email == remark)).scalar_one_or_none()
    if not user and str(user_id_raw).isdigit():
        user = db.get(User, int(str(user_id_raw)))

    tier_from = user.tier if user else ""
    months = 1
    if user:
        now = datetime.utcnow()
        base = user.tier_expires_at if user.tier_expires_at and user.tier_expires_at > now else now
        user.tier = tier_to
        user.tier_expires_at = base + timedelta(days=30 * months)
        db.add(user)

    db.add(
        Payment(
            user_id=user.id if user else None,
            order_id=order_id,
            plan=plan_id,
            amount_cny=amount_fen / 100,
            tier_from=tier_from,
            tier_to=tier_to,
            status="paid",
            raw_payload=payload,
        )
    )
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
