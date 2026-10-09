"""支付（爱发电）：回调自动开通/续期会员、付费记录查询。

签名校验（2026-10-09 与爱发电真实回调对拍确认）：RSA-SHA256，非 HMAC。
- 回调体为 JSON：{"data": {"type": "order", "order": {...}, "sign": "<base64>"}}。
- 待签名字符串 = out_trade_no + user_id + plan_id + total_amount
  （订单字段原样拼接，无分隔符；plan_id 为空时按空字符串）。
- sign 为爱发电用其私钥对上述字符串做 SHA256withRSA 签名后 base64；
  用爱发电官方公钥（AFDIAN_PUBLIC_KEY）验签。
- 金额字段 total_amount 为元字符串（如 "9.90"），解析为分后与档位价比对。

此前按社区文档写的 HMAC-SHA256(x-afdian-signature 头) 已被真实回调证伪
（爱发电根本不发该头），2026-10-09 改为上述 RSA 方案。

验签为 fail-closed：JSON 非法、data.type 非 order、字段缺失、签名无效
一律拒绝（403），绝不放行。

对账兜底：webhook 丢失 / 金额异常 / 退款无承接 → 设计见
docs/reviews/reconciliation-plan.md（首版只留文档，未实现）。
"""

import base64
import json
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
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
from app.core.timeutil import utcnow
from app.models.models import Payment, User
from app.services.lifecycle import resume_quota_exhausted_tasks, resume_tier_limited_tasks

router = APIRouter(tags=["pay"])
log = get_logger("pay")
settings = get_settings()

# 档位期望金额（单位：分）。standard=¥9.9，pro=¥19.9。
EXPECTED_AMOUNT_FEN = {"standard": 990, "pro": 1990}

# 档位高低：用于判断 webhook 是升级还是降级（升级立即生效、降级到期生效）
TIER_RANK = {"trial": 0, "free": 1, "standard": 2, "pro": 3}

# 金额异常待处理计数 key（admin 待处理视图展示，断裂-15）
AMOUNT_MISMATCH_PENDING_KEY = "payments_amount_mismatch_pending"


def _resolve_user(db: Session, remark: str, user_id_raw: str) -> User | None:
    """关联用户：优先 remark 中填写的 user_id / email，再看回调自带 user_id。

    R9-I3：remark 先 strip().lower() 再比对（register/login/resend 已统一
    归一化；爱发电备注里 " Foo@X.com " 不处理会查不到用户）。
    """
    remark = remark.strip().lower()
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
        {"key": AMOUNT_MISMATCH_PENDING_KEY, "now": utcnow()},
    ).first()
    return int(row[0]) if row else 0


def apply_tier_grant(db: Session, user: User, tier_to: str) -> str:
    """按 webhook/补单口径授予档位（可单独单元测试）。

    返回 "granted"（升级/续费立即生效）或 "downgrade_pending"（降级到期生效，
    只写 pending_tier，到期 sweep 再切换；不自洽-1）。
    配额锚点与会员周期对齐（购买日+30天滚动）。

    R6-D2：续费走原子 UPDATE——到期时间 = max(当前到期, now) + 30 天，
    一条 SQL 完成读-改-写。并发 webhook（不同订单）同时给同一用户续费时，
    不再出现"先读后写"的 lost-update（60 天变 30 天）。
    """
    now = utcnow()
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
    # now_s 与 SQLite 列内格式一致（"YYYY-MM-DD HH:MM:SS"，字典序可比）。
    # 注意：SQLite 多参数 max() 遇到 NULL 直接返回 NULL（不是忽略），所以先
    # coalesce(tier_expires_at, :now) 把首次购买的 NULL 转成 now 再取 max。
    now_s = now.strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        text(
            "UPDATE users SET tier = :tier, "
            "tier_expires_at = datetime(max(coalesce(tier_expires_at, :now), :now), '+30 days'), "
            "quota_reset_at = datetime(max(coalesce(tier_expires_at, :now), :now), '+30 days'), "
            "pending_tier = NULL "
            "WHERE id = :id"
        ),
        {"tier": tier_to, "now": now_s, "id": user.id},
    )
    # 原子 UPDATE 绕过 ORM：refresh 把内存对象与行同步，供调用方/日志使用
    db.refresh(user)
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


# 爱发电官方 RSA 公钥（公开信息，用于验签 webhook 的 data.sign）。
# 来源：爱发电开放平台文档（经 churchtao/daoyou 生产项目交叉验证）。
# 若爱发电轮换公钥导致验签全败，属 fail-closed（回调被拒、订单不丢——
# 可经开放 API 查单补录），更新此常量并重新对拍即可。
AFDIAN_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwwdaCg1Bt+UKZKs0R54y
lYnuANma49IpgoOwNmk3a0rhg/PQuhUJ0EOZSowIC44l0K3+fqGns3Ygi4AfmEfS
4EKbdk1ahSxu7Zkp2rHMt+R9GarQFQkwSS/5x1dYiHNVMiR8oIXDgjmvxuNes2Cr
8fw9dEF0xNBKdkKgG2qAawcN1nZrdyaKWtPVT9m2Hl0ddOO9thZmVLFOb9NVzgYf
jEgI+KWX6aY19Ka/ghv/L4t1IXmz9pctablN5S0CRWpJW3Cn0k6zSXgjVdKm4uN7
jRlgSRaf/Ind46vMCm3N2sgwxu/g3bnooW+db0iLo13zzuvyn727Q3UDQ0MmZcEW
MQIDAQAB
-----END PUBLIC KEY-----"""

# total_amount 为元字符串（"9.90"/"38"），严格格式防 "1.234"/"-1" 浑水摸鱼。
_CNY_RE = re.compile(r"^(0|[1-9]\d{0,9})(?:\.(\d{1,2}))?$")


def _parse_cny_to_fen(value: object) -> int | None:
    """爱发电 webhook 的 total_amount（元字符串）→ 分；格式非法返回 None。"""
    if not isinstance(value, str):
        value = str(value)
    m = _CNY_RE.match(value.strip())
    if not m:
        return None
    fen = int(m.group(1)) * 100 + int((m.group(2) or "").ljust(2, "0") or 0)
    return fen


def _afdian_public_key():
    return serialization.load_pem_public_key(AFDIAN_PUBLIC_KEY.encode("utf-8"))


def verify_afdian_webhook(payload: object) -> bool:
    """验签（fail-closed）：RSA-SHA256(data.sign) over 订单字段拼接串。

    任何异常（非 dict、type 非 order、字段缺失、base64 非法、验签不通过）
    一律返回 False，调用方统一 403。
    """
    # TEMP-DEBUG 2026-10-09：抓爱发电测试回调原文，定位验签失败原因；定位后删除
    try:
        _dbg_data = payload.get("data") if isinstance(payload, dict) else None
        _dbg_order = _dbg_data.get("order") if isinstance(_dbg_data, dict) else None
        log.warning(
            "afdian_debug_payload",
            top_keys=list(payload.keys()) if isinstance(payload, dict) else None,
            data_type=_dbg_data.get("type") if isinstance(_dbg_data, dict) else None,
            has_sign=bool(_dbg_data.get("sign")) if isinstance(_dbg_data, dict) else False,
            sign_len=len(_dbg_data.get("sign") or "") if isinstance(_dbg_data, dict) else 0,
            order_keys=list(_dbg_order.keys()) if isinstance(_dbg_order, dict) else None,
            out_trade_no=str((_dbg_order or {}).get("out_trade_no")),
            user_id=str((_dbg_order or {}).get("user_id")),
            plan_id=str((_dbg_order or {}).get("plan_id")),
            total_amount=str((_dbg_order or {}).get("total_amount")),
        )
    except Exception:
        pass
    try:
        if not isinstance(payload, dict):
            return False
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("type") != "order":
            return False
        order = data.get("order")
        if not isinstance(order, dict):
            return False
        sign_b64 = data.get("sign")
        if not isinstance(sign_b64, str) or not sign_b64:
            return False
        out_trade_no = str(order.get("out_trade_no") or "")
        if not out_trade_no:
            return False
        signed_text = (
            out_trade_no
            + str(order.get("user_id") or "")
            + str(order.get("plan_id") or "")
            + str(order.get("total_amount") or "")
        )
        signature = base64.b64decode(sign_b64)
        _afdian_public_key().verify(
            signature,
            signed_text.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except (InvalidSignature, ValueError, TypeError) as e:
        # TEMP-DEBUG 2026-10-09：定位后删除
        log.warning("afdian_debug_verify_fail", exc_type=type(e).__name__)
        return False


def _tier_by_plan() -> dict[str, str]:
    """plan_id -> tier 映射（延迟构建；plan_id 为空时不参与映射）。"""
    m: dict[str, str] = {}
    if settings.AFDIAN_PLAN_STANDARD:
        m[settings.AFDIAN_PLAN_STANDARD] = "standard"
    if settings.AFDIAN_PLAN_PRO:
        m[settings.AFDIAN_PLAN_PRO] = "pro"
    return m


async def _read_raw_body(request: Request) -> bytes:
    """R19-P3-3：同步端点内不能 await；用异步依赖在事件循环里先读出原始 body。

    FastAPI 允许同步端点挂异步依赖：依赖在事件循环中执行，端点本体跑在
    worker 线程，同步 SQLAlchemy Session 不再阻塞事件循环。
    """
    return await request.body()


@router.post("/pay/afdian-webhook")
def afdian_webhook(
    db: Session = Depends(get_db),
    raw: bytes = Depends(_read_raw_body),
):
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise APIError(400, "payload 不是合法 JSON", "bad_payload") from e
    if not verify_afdian_webhook(payload):
        log.warning("afdian_bad_signature")
        raise APIError(403, "签名校验失败", "bad_signature")

    data = payload.get("data") or {}
    order = data.get("order") or {}
    order_id = str(order.get("out_trade_no") or data.get("out_trade_no") or "")
    plan_id = str(order.get("plan_id") or data.get("plan_id") or "")
    user_id_raw = order.get("user_id") or data.get("user_id") or ""
    remark = str(order.get("remark") or data.get("remark") or "")
    # total_amount 为元字符串（如 "9.90"）；解析失败记为 None，下方走
    # amount_mismatch 落库待人工（amount_cny 记 0），返回 200。
    # R10-P2-1：解析放进 try——此前 ValueError → 500 → 爱发电无限重试黑洞。
    raw_amount = order.get("total_amount") or data.get("total_amount") or ""
    amount_fen = _parse_cny_to_fen(raw_amount)
    if amount_fen is None:
        log.error(
            "afdian_amount_unparsable", order_id=order_id, raw_amount=str(raw_amount)[:50]
        )

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
                amount_cny=(amount_fen / 100) if amount_fen is not None else 0,
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
    # R10-P2-1：amount_fen 为 None（解析失败）同样走金额异常待人工，不 500。
    if amount_fen is None or amount_fen != expected_fen:
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
                amount_cny=(amount_fen / 100) if amount_fen is not None else 0,
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
    notices: list[str] = []
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
        elif result == "granted":
            # R9-I14：升级/续费成功后，自动恢复因档位超限被暂停的任务
            resumed = resume_tier_limited_tasks(db, user)
            if resumed:
                notices.append(f"自动恢复 {len(resumed)} 个因档位超限被暂停的监控任务")
            # R13-P2-5：配额耗尽暂停的任务在新周期配额可用时一并恢复
            # （手动暂停的不动）
            resumed_q = resume_quota_exhausted_tasks(db, user)
            if resumed_q:
                notices.append(f"自动恢复 {len(resumed_q)} 个因配额耗尽被暂停的监控任务")

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
    return {"ok": True, "tier": tier_to, "user_id": user.id if user else None, "notices": notices}


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
