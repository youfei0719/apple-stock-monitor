"""round8 后端修复回归测试。

R8-B0-3  admin 补单 tier_expires_at/quota_reset_at 传 tz-aware → 入库前归一化为
         naive UTC（_as_naive_utc 已提升到 app.core.timeutil 公共模块）
R8-I-6   claim_payment 无订单状态守卫 → refunded/resolved 等终态订单不可认领
R8-I-7   /notify/test 每日限额改北京时间零点口径（复用 lifecycle._today_start）
R8-I-8   admin 用户搜索 LIKE 通配符转义 + 默认+30天审计 from 记旧值
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.errors import APIError
from app.api.routers import admin as admin_router
from app.api.routers import notify as notify_router
from app.api.routers import tasks as tasks_router
from app.core import timeutil
from app.core.db import Base
from app.models.models import Notification, Payment, User


@pytest.fixture
def db():
    e = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e)
    s = sessionmaker(bind=e)()
    yield s
    s.close()


def _req(headers=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(scope={"type": "http", "headers": raw, "client": ("9.9.9.9", 1234)})


def _user(db, tier="free", email="r8@example.com", days_left=None, **kw):
    exp = datetime.utcnow() + timedelta(days=days_left) if days_left is not None else None
    u = User(
        email=email,
        password_hash="x",
        tier=tier,
        tier_expires_at=exp,
        quota_reset_at=datetime.utcnow() + timedelta(days=30),
        email_verified=True,
        **kw,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _payment(db, status, tier_to="standard", order_id=None, user_id=None):
    p = Payment(
        order_id=order_id or f"r8-{status}-{id(db) % 100000}",
        plan="standard_monthly",
        amount_cny=19.0,
        tier_from="free",
        tier_to=tier_to,
        status=status,
        user_id=user_id,
        raw_payload={},
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ---------- R8-B0-3：归一化已提升到公共模块 ----------
def test_as_naive_utc_moved_to_timeutil():
    # tasks 里的别名即公共模块函数（实现只存在一份）
    assert tasks_router._as_naive_utc is timeutil.as_naive_utc
    aware = datetime(2026, 12, 1, 8, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    out = timeutil.as_naive_utc(aware)
    assert out.tzinfo is None
    assert out == datetime(2026, 12, 1, 0, 0, 0)


def test_patch_user_tzaware_plus8_normalized_to_utc_naive(db):
    """报告实测场景：传 "2026-12-01T08:00:00+08:00"（意图 UTC 0 点），
    此前入库 naive 2026-12-01T08:00:00（偏差 8 小时）；修复后应入库
    naive 2026-12-01T00:00:00，配额锚点同步跟上。"""
    admin = _user(db, email="admin@example.com", is_admin=True)
    u = _user(db, tier="standard", email="paid@example.com", days_left=10)
    aware = datetime(2026, 12, 1, 8, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    out = admin_router.patch_user(
        u.id,
        admin_router.AdminUserPatchEx(tier_expires_at=aware),
        _req(),
        admin,
        db,
    )
    db.refresh(u)
    expected = datetime(2026, 12, 1, 0, 0, 0)
    assert u.tier_expires_at == expected
    assert u.tier_expires_at.tzinfo is None
    # 配额锚点同样归一化（此前同样错 8 小时）
    assert u.quota_reset_at == expected
    # 审计记录的是归一化后的值
    assert out["changes"]["tier_expires_at"]["to"] == expected.isoformat()


def test_patch_user_naive_input_unchanged(db):
    admin = _user(db, email="admin2@example.com", is_admin=True)
    u = _user(db, tier="standard", email="paid2@example.com", days_left=10)
    naive = datetime(2026, 12, 1, 8, 0, 0)
    admin_router.patch_user(
        u.id, admin_router.AdminUserPatchEx(tier_expires_at=naive), _req(), admin, db
    )
    db.refresh(u)
    assert u.tier_expires_at == naive  # naive 输入保持原样，不做换算


# ---------- R8-I-6：claim_payment 订单状态守卫 ----------
def test_claim_refunded_payment_rejected(db):
    admin = _user(db, email="admin3@example.com", is_admin=True)
    u = _user(db, email="buyer@example.com")
    p = _payment(db, "refunded", order_id="r8-refunded-1")
    with pytest.raises(APIError) as exc:
        admin_router.claim_payment(
            p.id, admin_router.PaymentClaimIn(user_id=u.id), _req(), admin, db
        )
    assert exc.value.status_code == 400
    assert exc.value.code == "bad_status"


def test_claim_resolved_payment_rejected(db):
    admin = _user(db, email="admin4@example.com", is_admin=True)
    u = _user(db, email="buyer2@example.com")
    p = _payment(db, "resolved", order_id="r8-resolved-1")
    with pytest.raises(APIError) as exc:
        admin_router.claim_payment(
            p.id, admin_router.PaymentClaimIn(user_id=u.id), _req(), admin, db
        )
    assert exc.value.status_code == 400
    assert exc.value.code == "bad_status"
    db.refresh(u)
    assert u.tier == "free"  # 幽灵会员未被开通


def test_claim_paid_payment_still_works(db):
    """守卫不误伤正常认领：paid 订单仍可认领。"""
    admin = _user(db, email="admin5@example.com", is_admin=True)
    u = _user(db, email="buyer3@example.com")
    p = _payment(db, "paid", order_id="r8-paid-1")
    out = admin_router.claim_payment(
        p.id, admin_router.PaymentClaimIn(user_id=u.id), _req(), admin, db
    )
    assert out["ok"] is True
    db.refresh(u)
    assert u.tier == "standard"
    db.refresh(p)
    assert p.user_id == u.id


# ---------- R8-I-8：LIKE 转义 + 审计 from ----------
def test_list_users_search_escapes_like_wildcards(db):
    admin = _user(db, email="admin6@example.com", is_admin=True)
    _user(db, email="ab@example.com")
    _user(db, email="a%b@example.com")
    out = admin_router.list_users(q="%", tier=None, limit=50, admin=admin, db=db)
    emails = [r["email"] for r in out]
    # 未转义时 "%" 会匹配全部用户；转义后只命中字面含 % 的邮箱
    assert emails == ["a%b@example.com"]


def test_list_users_search_still_matches_normal(db):
    admin = _user(db, email="admin7@example.com", is_admin=True)
    _user(db, email="zz-top@example.com")
    out = admin_router.list_users(q="zz-top", tier=None, limit=50, admin=admin, db=db)
    assert [r["email"] for r in out] == ["zz-top@example.com"]


def test_patch_default_30d_audit_from_records_old_expired_value(db):
    """默认+30天时审计 from 应记旧值（已过期时间戳），此前硬编码 None。"""
    admin = _user(db, email="admin8@example.com", is_admin=True)
    u = _user(db, tier="pro", email="expired@example.com", days_left=-5)
    old_iso = u.tier_expires_at.isoformat()
    out = admin_router.patch_user(
        u.id, admin_router.AdminUserPatchEx(tier="pro"), _req(), admin, db
    )
    assert out["changes"]["tier_expires_at"]["from"] == old_iso


# ---------- R8-I-7：/notify/test 每日限额北京时间口径 ----------
class _FakeNotifier:
    def __init__(self, db):
        pass

    def test_channel(self, channel, target, user_id=None):
        return SimpleNamespace(status="sent", error=None, id=999)


def _seed_test_notifications(db, user_id, created_at, n):
    for _ in range(n):
        db.add(
            Notification(
                user_id=user_id,
                kind="test",
                channel="email",
                target="t",
                title="t",
                body="b",
                status="sent",
                created_at=created_at,
            )
        )
    db.commit()


def test_notify_test_daily_limit_beijing_boundary(db, monkeypatch):
    """UTC 2026-10-08 20:00 = 北京时间 10-09 04:00（今天）：
    UTC 口径下它算"昨天"（不计入），北京时间口径下应计入今天 → 第 6 次被限。"""
    u = _user(db, email="notify@example.com")
    _seed_test_notifications(db, u.id, datetime(2026, 10, 8, 20, 0, 0), 5)
    monkeypatch.setattr(notify_router, "_utcnow", lambda: datetime(2026, 10, 9, 2, 0, 0))
    monkeypatch.setattr(notify_router, "Notifier", _FakeNotifier)
    with pytest.raises(APIError) as exc:
        notify_router.notify_test(
            notify_router.NotifyTestIn(channel="email", target=u.email), u, db
        )
    assert exc.value.status_code == 429
    assert exc.value.code == "test_limited"


def test_notify_test_daily_limit_not_triggered_before_cap(db, monkeypatch):
    u = _user(db, email="notify2@example.com")
    _seed_test_notifications(db, u.id, datetime(2026, 10, 8, 20, 0, 0), 4)
    monkeypatch.setattr(notify_router, "_utcnow", lambda: datetime(2026, 10, 9, 2, 0, 0))
    monkeypatch.setattr(notify_router, "Notifier", _FakeNotifier)
    out = notify_router.notify_test(
        notify_router.NotifyTestIn(channel="email", target=u.email), u, db
    )
    assert out["ok"] is True


# ---------- 后-D-2：_require_channels 按档位校验渠道 ----------
def test_require_channels_standard_bark_rejected(db):
    """standard 档配 bark_key → 400（没有任何档位支持 bark，配了也永远不响）。"""
    u = _user(db, tier="standard", email="std@example.com", days_left=10)
    with pytest.raises(APIError) as exc:
        tasks_router._require_channels({"bark_key": "abc123"}, u)
    assert exc.value.status_code == 400
    assert exc.value.code == "channel_not_supported"
    assert "Bark" in exc.value.detail


def test_require_channels_standard_wecom_rejected(db):
    u = _user(db, tier="standard", email="std2@example.com", days_left=10)
    channels = {"webhooks": [{"platform": "wecom", "url": "https://qyapi.weixin.qq.com/x"}]}
    with pytest.raises(APIError) as exc:
        tasks_router._require_channels(channels, u)
    assert exc.value.status_code == 400
    assert exc.value.code == "channel_not_supported"
    assert "企微" in exc.value.detail


def test_require_channels_standard_email_ok(db):
    u = _user(db, tier="standard", email="std3@example.com", days_left=10)
    tasks_router._require_channels({"email": "std3@example.com"}, u)  # 不抛即通过


def test_require_channels_empty_non_trial_only_recommends_email(db):
    """空渠道文案只推荐邮箱（Bark/群机器人永远不响，推荐它们是误导）。"""
    u = _user(db, tier="free", email="free@example.com")
    with pytest.raises(APIError) as exc:
        tasks_router._require_channels({}, u)
    assert exc.value.status_code == 400
    assert exc.value.code == "channels_required"
    assert "邮箱" in exc.value.detail
    assert "Bark" not in exc.value.detail


def test_require_channels_trial_email_rejected(db):
    """trial 仅支持站内（与 /notify/test 口径一致），配 email 也 400。"""
    with pytest.raises(APIError) as exc:
        tasks_router._require_channels({"email": "anon@example.com"}, None)
    assert exc.value.status_code == 400
    assert exc.value.code == "channel_not_supported"


def test_require_channels_trial_empty_ok():
    """trial 空渠道仍放行（走站内 page 触达，不要求外部渠道）。"""
    tasks_router._require_channels({}, None)  # 不抛即通过
