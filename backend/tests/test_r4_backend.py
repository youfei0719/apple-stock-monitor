"""round4 后端修复回归测试。"""

import os
import sys
from datetime import timedelta

import pytest
from fastapi import Request
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.routers import admin as admin_router
from app.api.routers import auth as auth_router
from app.api.routers import pay as pay_router
from app.core.db import Base
from app.core.timeutil import utcnow
from app.models.models import MonitorTask, Payment, SystemConfig, User
from app.schemas import TaskBatchIn
from app.services import lifecycle as lifecycle_mod
from app.services.apple_client import classify


@pytest.fixture
def db():
    e = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e)
    s = sessionmaker(bind=e)()
    yield s
    s.close()


def _req():
    return Request(scope={"type": "http", "headers": [], "client": ("9.9.9.9", 1234)})


def _user(db, tier="free", email="r4@example.com", days_left=None):
    exp = utcnow() + timedelta(days=days_left) if days_left is not None else None
    u = User(
        email=email,
        password_hash="x",
        tier=tier,
        tier_expires_at=exp,
        quota_reset_at=utcnow() + timedelta(days=30),
        email_verified=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _admin(db):
    a = _user(db, tier="pro", email="admin@example.com")
    a.is_admin = True
    db.add(a)
    db.commit()
    return a


# ---- P0-3: refund 递减 amount_mismatch 计数 ----
def test_refund_decrements_amount_mismatch_pending(db):
    admin = _admin(db)
    u = _user(db, email="buyer@example.com")
    db.add(SystemConfig(key="payments_amount_mismatch_pending", value={"count": 1}))
    p = Payment(
        user_id=u.id,
        order_id="OID-R4-1",
        plan="p1",
        amount_cny=19,
        tier_from="free",
        tier_to="standard",
        status="amount_mismatch",
        raw_payload={},
    )
    db.add(p)
    db.commit()
    out = admin_router.refund_payment(p.id, _req(), admin, db)
    assert out["payment_status"] == "refunded"
    assert out["pending_count"] == 0
    row = db.execute(
        select(SystemConfig).where(
            SystemConfig.key == "payments_amount_mismatch_pending"
        )
    ).scalar_one()
    assert row.value["count"] == 0


# ---- B7/B8: refund 重置 quota_reset_at + 收敛任务 ----
def test_refund_resets_quota_and_converges_tasks(db):
    admin = _admin(db)
    u = _user(db, tier="pro", email="pro@example.com", days_left=10)
    old_anchor = u.quota_reset_at
    for i in range(5):
        db.add(MonitorTask(user_id=u.id, name=f"t{i}", part_number="P", store_numbers=["R1"]))
    p = Payment(
        user_id=u.id,
        order_id="OID-R4-2",
        plan="p1",
        amount_cny=39,
        tier_from="standard",
        tier_to="pro",
        status="paid",
        raw_payload={},
    )
    db.add(p)
    db.commit()
    out = admin_router.refund_payment(p.id, _req(), admin, db)
    db.refresh(u)
    assert u.tier == "free"
    assert u.tier_expires_at is None
    assert u.pending_tier is None
    # B7: 锚点重置为 now+30 天
    assert u.quota_reset_at > old_anchor
    assert u.quota_reset_at > utcnow() + timedelta(days=29)
    # B8: free 上限 3，5 个任务 → 暂停 2 个
    assert out["user"]["tasks_paused"] == 2
    active = db.execute(
        select(MonitorTask).where(
            MonitorTask.user_id == u.id, MonitorTask.paused.is_(False)
        )
    ).scalars().all()
    assert len(active) == 3


# ---- D2: PATCH tier=free 清空 expires/pending ----
def test_patch_user_free_clears_expiry(db):
    admin = _admin(db)
    u = _user(db, tier="standard", email="std@example.com", days_left=10)
    u.pending_tier = "free"
    db.add(u)
    db.commit()
    data = admin_router.AdminUserPatchEx(tier="free")
    admin_router.patch_user(u.id, data, _req(), admin, db)
    db.refresh(u)
    assert u.tier == "free"
    assert u.tier_expires_at is None
    assert u.pending_tier is None


# ---- D3: claim 复用 apply_tier_grant（降级到期生效） ----
def test_claim_payment_downgrade_pending(db):
    admin = _admin(db)
    u = _user(db, tier="pro", email="pro2@example.com", days_left=10)
    p = Payment(
        user_id=None,
        order_id="OID-R4-3",
        plan="p1",
        amount_cny=19,
        tier_from="",
        tier_to="standard",
        status="paid",
        raw_payload={},
    )
    db.add(p)
    db.commit()
    out = admin_router.claim_payment(
        p.id, admin_router.PaymentClaimIn(user_id=u.id), _req(), admin, db
    )
    db.refresh(u)
    # pro 在效期用户认领 standard 订单：不立即降级，走 pending_tier
    assert u.tier == "pro"
    assert u.pending_tier == "standard"
    assert out["pending_tier"] == "standard"


# ---- D1: sweep 晋升 pending_tier 同步 quota_reset_at ----
def test_membership_sweep_pending_promotion_syncs_quota(db):
    u = _user(db, tier="pro", email="pro3@example.com", days_left=-1)
    u.pending_tier = "standard"
    old_anchor = u.quota_reset_at
    db.add(u)
    db.commit()
    stats = lifecycle_mod.membership_sweep(db)
    assert stats["pending_promoted"] == 1
    db.refresh(u)
    assert u.tier == "standard"
    assert u.pending_tier is None
    assert u.quota_reset_at > old_anchor
    assert u.quota_reset_at > utcnow() + timedelta(days=29)


# ---- B4: api_hits 保留策略 ----
def test_prune_api_hits(db):
    from app.models.models import ApiHit

    db.add(ApiHit(path="/api/x", ip_hash="a" * 32))
    old = ApiHit(path="/api/y", ip_hash="b" * 32)
    old.created_at = utcnow() - timedelta(days=100)
    db.add(old)
    db.commit()
    out = lifecycle_mod.prune_api_hits(db)
    assert out["pruned"] == 1
    assert db.query(ApiHit).count() == 1


# ---- B1: 邮箱归一化（register 入口） ----
def test_register_email_normalized(db):
    from app.schemas import RegisterIn

    req = Request(
        scope={
            "type": "http",
            "headers": [(b"x-device-id", b"dev-r4")],
            "client": ("9.9.9.9", 1234),
        }
    )
    out = auth_router.register(RegisterIn(email="  Foo@Example.COM ", password="x1a2b3c4d5e6"), req, db)
    assert out.email == "foo@example.com"
    # 同邮箱换大小写再注册 → 400 email_taken（不会建成两个账号）
    from app.api.errors import APIError

    with pytest.raises(APIError) as ei:
        auth_router.register(RegisterIn(email="FOO@example.com", password="y1a2b3c4d5e6"), req, db)
    assert ei.value.code == "email_taken"


# ---- P2: classify 缺字段判 unknown ----
def test_classify_missing_fields_unknown():
    assert classify(None, None) == "unknown"
    assert classify("available", None) == "unknown"
    assert classify("available", True) == "available"
    assert classify("unavailable", True) == "unavailable"


# ---- B2: TaskBatchIn 上限 ----
def test_task_batch_in_limits():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TaskBatchIn(part_numbers=[f"P{i}" for i in range(31)], store_numbers=["R1"])
    ok = TaskBatchIn(part_numbers=[f"P{i}" for i in range(30)], store_numbers=["R1"])
    assert len(ok.part_numbers) == 30


# ---- D5: 未知 plan_id 落库 unknown_plan 返回 200 ----
def _test_rsa_keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode("utf-8")
    )
    return key, pub_pem


_TEST_PRIV_KEY, _TEST_PUB_PEM = _test_rsa_keypair()


def _signed_webhook_raw(order: dict) -> bytes:
    """按爱发电真实协议构造签名回调体：RSA-SHA256(data.sign)。"""
    import base64
    import json

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    signed_text = (
        str(order.get("out_trade_no") or "")
        + str(order.get("user_id") or "")
        + str(order.get("plan_id") or "")
        + str(order.get("total_amount") or "")
    )
    sig = _TEST_PRIV_KEY.sign(signed_text.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    payload = {"data": {"type": "order", "order": order, "sign": base64.b64encode(sig).decode()}}
    return json.dumps(payload).encode("utf-8")


def test_unknown_plan_recorded(db, monkeypatch):
    monkeypatch.setattr(pay_router, "AFDIAN_PUBLIC_KEY", _TEST_PUB_PEM)
    order = {
        "out_trade_no": "OID-R4-UNKNOWN",
        "user_id": "12345",
        "plan_id": "no-such-plan",
        "total_amount": "19.00",
        "remark": "",
    }
    raw = _signed_webhook_raw(order)
    out = pay_router.afdian_webhook(db, raw)
    assert out["ok"] is True
    assert out["status"] == "unknown_plan"
    p = db.execute(
        select(Payment).where(Payment.order_id == "OID-R4-UNKNOWN")
    ).scalar_one()
    assert p.status == "unknown_plan"
    row = db.execute(
        select(SystemConfig).where(
            SystemConfig.key == "payments_amount_mismatch_pending"
        )
    ).scalar_one()
    assert row.value["count"] == 1


# ---- 前端联动：list_users 返回 email_verified ----
def test_list_users_returns_email_verified(db):
    admin = _admin(db)
    u = _user(db, email="v@example.com")
    u.email_verified = False
    db.add(u)
    db.commit()
    rows = admin_router.list_users(None, None, 50, admin, db)
    by_email = {r["email"]: r for r in rows}
    assert by_email["v@example.com"]["email_verified"] is False
    assert by_email["admin@example.com"]["email_verified"] is True


# ---- P2: _due_tasks 索引存在 ----
def test_due_tasks_index_exists(db):
    from sqlalchemy import text

    rows = db.execute(
        text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='monitor_tasks'")
    ).fetchall()
    names = {r[0] for r in rows}
    assert "ix_monitor_tasks_paused_expires" in names


# ---- P2: period 列宽 ----
def test_quota_period_column_width():
    from app.models.models import QuotaUsage

    assert QuotaUsage.period.property.columns[0].type.length == 10


# ---- RSA 验签（2026-10-09 真实回调对拍后的协议） ----
def _order(**kw):
    base = {
        "out_trade_no": "OID-RSA-1",
        "user_id": "u123",
        "plan_id": "p456",
        "total_amount": "9.90",
    }
    base.update(kw)
    return base


def test_verify_afdian_webhook_ok(monkeypatch):
    monkeypatch.setattr(pay_router, "AFDIAN_PUBLIC_KEY", _TEST_PUB_PEM)
    raw = _signed_webhook_raw(_order())
    import json

    assert pay_router.verify_afdian_webhook(json.loads(raw)) is True


def test_verify_afdian_webhook_tampered_amount(monkeypatch):
    """金额被篡改 → 验签失败（fail-closed）。"""
    import json

    monkeypatch.setattr(pay_router, "AFDIAN_PUBLIC_KEY", _TEST_PUB_PEM)
    raw = _signed_webhook_raw(_order())
    payload = json.loads(raw)
    payload["data"]["order"]["total_amount"] = "999.00"
    assert pay_router.verify_afdian_webhook(payload) is False


def test_verify_afdian_webhook_missing_sign():
    import json

    raw = _signed_webhook_raw(_order())
    payload = json.loads(raw)
    del payload["data"]["sign"]
    assert pay_router.verify_afdian_webhook(payload) is False


def test_verify_afdian_webhook_wrong_type():
    import json

    raw = _signed_webhook_raw(_order())
    payload = json.loads(raw)
    payload["data"]["type"] = "refund"
    assert pay_router.verify_afdian_webhook(payload) is False


def test_verify_afdian_webhook_not_dict():
    assert pay_router.verify_afdian_webhook([]) is False
    assert pay_router.verify_afdian_webhook(None) is False


def test_webhook_bad_signature_returns_403(db, monkeypatch):
    """伪造回调 → 403，不落库。"""
    from app.api.errors import APIError

    monkeypatch.setattr(pay_router, "AFDIAN_PUBLIC_KEY", _TEST_PUB_PEM)
    order = _order(out_trade_no="OID-RSA-EVIL")
    order["total_amount"] = "9.90"
    # 用同样的 key 签名但随后篡改金额 → 签名对不上
    import json

    raw = _signed_webhook_raw(order)
    payload = json.loads(raw)
    payload["data"]["order"]["total_amount"] = "0.01"
    raw2 = json.dumps(payload).encode()
    with pytest.raises(APIError) as ei:
        pay_router.afdian_webhook(db, raw2)
    assert ei.value.status_code == 403
    n = db.execute(select(Payment).where(Payment.order_id == "OID-RSA-EVIL")).scalar_one_or_none()
    assert n is None


def test_parse_cny_to_fen():
    p = pay_router._parse_cny_to_fen
    assert p("9.90") == 990
    assert p("19.9") == 1990
    assert p("38") == 3800
    assert p("0.01") == 1
    assert p("abc") is None
    assert p("1.234") is None
    assert p("-1") is None
    assert p("") is None
