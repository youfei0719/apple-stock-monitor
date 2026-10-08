"""round11 后端修复回归测试。

R11-P1-5 quota.py 匿名 trial 月 key 改北京时间口径，与 engine 共用
        trial_month_key()（此前 quota.py 用 UTC 月份，每月 1 日 0:00–8:00
        展示的 push_used 与引擎扣减错位，trialExhausted 横幅误判）
R11-P2-1 membership_sweep 降 free 时 quota_reset_at 置 now+30d（与
        refund/手动降档同口径，不再置 NULL）
R11-P2-2 patch_user：tier 为 free/trial 时传 tier_expires_at → 400（不再造
        脏状态）；补单分支仅付费档位可设到期时间
R11-P2-3 engine 自动暂停文案不再提 Bark key / webhook
R11-P2-4 死代码删除：schemas.ErrorOut 零引用；_strip_channel_secrets 不再
        pop sms_to
"""

import os
import sys
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.errors import APIError  # noqa: E402
from app.api.routers import admin as admin_router  # noqa: E402
from app.api.routers import auth as auth_router  # noqa: E402
from app.api.routers import quota as quota_router  # noqa: E402
from app.core.db import Base  # noqa: E402
from app.models.models import User  # noqa: E402
from app.services import engine as engine_mod  # noqa: E402
from app.services.lifecycle import membership_sweep  # noqa: E402


@pytest.fixture
def db():
    e = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e)
    s = sessionmaker(bind=e)()
    yield s
    s.close()


def _req(ip="9.9.9.9", headers=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(scope={"type": "http", "headers": raw, "client": (ip, 1234)})


def _user(db, tier="free", email=None, days_left=None, **kw):
    exp = datetime.utcnow() + timedelta(days=days_left) if days_left is not None else None
    u = User(
        email=email or f"r11-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        tier=tier,
        tier_expires_at=exp,
        quota_reset_at=kw.pop("quota_reset_at", datetime.utcnow() + timedelta(days=30)),
        email_verified=True,
        **kw,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _admin(db):
    a = _user(db, tier="pro")
    a.is_admin = True
    db.add(a)
    db.commit()
    return a


# ============ R11-P1-5：匿名 trial 月 key 北京时间口径 ============
def test_trial_month_key_is_beijing_month():
    expect = (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m")
    assert engine_mod.trial_month_key() == expect


def test_quota_anonymous_period_uses_beijing_key(db):
    # quota.py 匿名分支与 engine._trial_quota_state 同口径
    out = quota_router.get_quota(user=None, db=db, x_device_id=f"dev-{uuid.uuid4().hex[:8]}")
    assert out["period"] == engine_mod.trial_month_key()


# ============ R11-P2-1：sweep 降 free 置 now+30d ============
def test_sweep_downgrade_to_free_sets_quota_anchor(db):
    u = _user(db, tier="standard", days_left=-1, quota_reset_at=None)  # 已过期
    before = datetime.utcnow()
    out = membership_sweep(db)
    assert out["downgraded"] == 1
    db.refresh(u)
    assert u.tier == "free"
    assert u.quota_reset_at is not None
    delta = u.quota_reset_at - before
    assert timedelta(days=29, hours=23) < delta < timedelta(days=30, hours=1)


def test_sweep_pending_promote_still_plus30d(db):
    u = _user(db, tier="standard", days_left=-1, pending_tier="pro")
    membership_sweep(db)
    db.refresh(u)
    assert u.tier == "pro"
    assert u.quota_reset_at is not None
    assert u.quota_reset_at - datetime.utcnow() > timedelta(days=29)


# ============ R11-P2-2：patch_user 脏状态拒绝 ============
def test_patch_user_free_with_expires_at_400(db):
    admin = _admin(db)
    u = _user(db, tier="standard", days_left=10)
    with pytest.raises(APIError) as e:
        admin_router.patch_user(
            u.id,
            admin_router.AdminUserPatchEx(
                tier="free", tier_expires_at=datetime.utcnow() + timedelta(days=30)
            ),
            _req(),
            admin,
            db,
        )
    assert e.value.status_code == 400
    assert e.value.code == "bad_expires_at"


def test_patch_user_trial_with_expires_at_400(db):
    admin = _admin(db)
    u = _user(db, tier="standard", days_left=10)
    with pytest.raises(APIError) as e:
        admin_router.patch_user(
            u.id,
            admin_router.AdminUserPatchEx(
                tier="trial", tier_expires_at=datetime.utcnow() + timedelta(days=30)
            ),
            _req(),
            admin,
            db,
        )
    assert e.value.status_code == 400


def test_patch_user_makeup_expires_on_free_user_400(db):
    admin = _admin(db)
    u = _user(db, tier="free")  # 仅传 tier_expires_at 的补单分支
    with pytest.raises(APIError) as e:
        admin_router.patch_user(
            u.id,
            admin_router.AdminUserPatchEx(tier_expires_at=datetime.utcnow() + timedelta(days=30)),
            _req(),
            admin,
            db,
        )
    assert e.value.status_code == 400
    db.refresh(u)
    assert u.tier_expires_at is None  # 未造脏状态


def test_patch_user_makeup_expires_on_paid_user_ok(db):
    admin = _admin(db)
    u = _user(db, tier="standard", days_left=10)
    new_exp = datetime.utcnow() + timedelta(days=60)
    admin_router.patch_user(
        u.id, admin_router.AdminUserPatchEx(tier_expires_at=new_exp), _req(), admin, db
    )
    db.refresh(u)
    assert abs((u.tier_expires_at - new_exp).total_seconds()) < 5
    assert abs((u.quota_reset_at - new_exp).total_seconds()) < 5


# ============ R11-P2-3：自动暂停文案 ============
def test_auto_pause_message_no_bark_webhook():
    import inspect

    src = inspect.getsource(engine_mod.Engine._fire)
    assert "Bark key" not in src and "webhook" not in src
    assert "邮箱" in src


# ============ R11-P2-4：死代码删除 ============
def test_error_out_removed():
    import app.schemas as schemas_mod

    assert not hasattr(schemas_mod, "ErrorOut")


def test_strip_channel_secrets_no_sms_to():
    import inspect

    src = inspect.getsource(auth_router._strip_channel_secrets)
    assert "sms_to" not in src


def test_strip_channel_secrets_still_strips():
    ch, had = auth_router._strip_channel_secrets(
        {"bark_key": "k", "email": "a@b.c", "webhooks": ["https://x"]}
    )
    assert had is True
    assert "bark_key" not in ch and "email" not in ch
    assert ch["webhooks"] == []
