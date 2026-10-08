"""round7 后端修复回归测试。

R7-1  tz-aware 归一化（_as_naive_utc / _validate_expires_at / _clamp_expires）
R7-2  admin 补单只 PATCH tier_expires_at 时同步 quota_reset_at
R7-6  lifecycle 按天去重改北京时间口径
R7-7  admin 批量暂停写 paused_reason="manual"，自动恢复只认 quota_exhausted
R7-4  幂等：任务被删后同 key 走 409（TTL 内），而非"按新请求处理"
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.errors import APIError
from app.api.routers import admin as admin_router
from app.api.routers import auth as auth_router
from app.api.routers import tasks as tasks_router
from app.core.db import Base
from app.core.timeutil import utcnow
from app.models.models import MonitorTask, Notification, User
from app.services import lifecycle as lifecycle_mod


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


def _user(db, tier="free", email="r7@example.com", days_left=None, **kw):
    exp = utcnow() + timedelta(days=days_left) if days_left is not None else None
    u = User(
        email=email,
        password_hash="x",
        tier=tier,
        tier_expires_at=exp,
        quota_reset_at=utcnow() + timedelta(days=30),
        email_verified=True,
        **kw,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ---------- R7-1：tz-aware 归一化 ----------
def test_as_naive_utc_aware():
    aware = datetime(2026, 12, 1, 8, 0, 0, tzinfo=timezone.utc)
    out = tasks_router._as_naive_utc(aware)
    assert out.tzinfo is None
    assert out == datetime(2026, 12, 1, 8, 0, 0)


def test_as_naive_utc_offset_zone():
    # +08:00 的 aware 时间应先转 UTC 再去 tzinfo
    aware = datetime(2026, 12, 1, 8, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    out = tasks_router._as_naive_utc(aware)
    assert out.tzinfo is None
    assert out == datetime(2026, 12, 1, 0, 0, 0)


def test_as_naive_utc_naive_and_none():
    naive = datetime(2026, 12, 1, 8, 0, 0)
    assert tasks_router._as_naive_utc(naive) == naive
    assert tasks_router._as_naive_utc(None) is None


def test_validate_expires_at_aware_future_no_typeerror():
    """pydantic 解析 "2026-12-01T00:00:00Z" 得 aware datetime——
    此前 aware < naive 直接 TypeError→500，现在应正常通过。"""
    aware_future = datetime.now(timezone.utc) + timedelta(days=30)
    tasks_router._validate_expires_at(aware_future)  # 不抛即通过


def test_validate_expires_at_aware_past_400_not_500():
    aware_past = datetime.now(timezone.utc) - timedelta(days=1)
    with pytest.raises(APIError) as exc:
        tasks_router._validate_expires_at(aware_past)
    assert exc.value.status_code == 400
    assert exc.value.code == "bad_expires_at"


def test_clamp_expires_aware_anonymous():
    aware = datetime.now(timezone.utc) + timedelta(hours=1)
    out = tasks_router._clamp_expires(aware, anonymous=True)
    assert out.tzinfo is None
    assert out <= utcnow() + timedelta(hours=24)


def test_clamp_expires_aware_non_anonymous_normalized():
    aware = datetime(2026, 12, 1, 8, 0, 0, tzinfo=timezone.utc)
    out = tasks_router._clamp_expires(aware, anonymous=False)
    assert out == datetime(2026, 12, 1, 8, 0, 0)
    assert out.tzinfo is None


# ---------- R7-2：补单同步配额锚点 ----------
def test_patch_only_tier_expires_at_syncs_quota_reset_at(db):
    admin = _user(db, email="admin@example.com", is_admin=True)
    u = _user(db, tier="standard", email="paid@example.com", days_left=10)
    new_exp = utcnow() + timedelta(days=60)
    out = admin_router.patch_user(
        u.id,
        admin_router.AdminUserPatchEx(tier_expires_at=new_exp),
        _req(),
        admin,
        db,
    )
    db.refresh(u)
    assert out["changes"]["tier_expires_at"]["to"] == new_exp.isoformat()
    # 补单只改到期时间时，配额锚点同步跟上
    assert u.quota_reset_at == u.tier_expires_at
    assert out["changes"]["quota_reset_at"] == u.tier_expires_at.isoformat()


def test_patch_tier_and_expires_at_keeps_paid_quota_alignment(db):
    admin = _user(db, email="admin2@example.com", is_admin=True)
    u = _user(db, tier="free", email="free@example.com")
    new_exp = utcnow() + timedelta(days=30)
    admin_router.patch_user(
        u.id,
        admin_router.AdminUserPatchEx(tier="pro", tier_expires_at=new_exp),
        _req(),
        admin,
        db,
    )
    db.refresh(u)
    assert u.tier == "pro"
    assert u.quota_reset_at == new_exp


# ---------- R7-6：按天去重北京时间口径 ----------
def test_today_start_beijing():
    # UTC 2026-10-09 02:00 = 北京时间 10:00，"今天"起点应为 UTC 10-08 16:00
    now = datetime(2026, 10, 9, 2, 0, 0)
    assert lifecycle_mod._today_start(now) == datetime(2026, 10, 8, 16, 0, 0)


def test_notified_today_beijing_boundary(db):
    """北京时间 00:30（UTC 前一天 16:30）的通知，在北京时间"今天"口径下
    应算已通知——旧 UTC 口径会漏算导致跨天重复提醒。"""
    u = _user(db, email="bj@example.com")
    # 2026-10-08 16:30 UTC = 北京时间 2026-10-09 00:30
    n = Notification(
        user_id=u.id,
        kind="membership_expiring",
        channel="system",
        target="",
        title="t",
        body="b",
        status="sent",
        created_at=datetime(2026, 10, 8, 16, 30, 0),
    )
    db.add(n)
    db.commit()
    # 用 monkeypatch 固定 _utcnow 为北京时间 10-09 10:00
    orig = lifecycle_mod._utcnow
    lifecycle_mod._utcnow = lambda: datetime(2026, 10, 9, 2, 0, 0)
    try:
        got = lifecycle_mod._notified_today_user_ids(db, "membership_expiring", [u.id])
    finally:
        lifecycle_mod._utcnow = orig
    assert u.id in got


# ---------- R7-7：批量暂停 manual + 自动恢复排除 ----------
def test_admin_batch_pause_writes_manual_reason(db):
    admin = _user(db, email="admin3@example.com", is_admin=True)
    u = _user(db, tier="free", email="t@example.com")
    t = MonitorTask(user_id=u.id, name="t", category="iphone", part_number="P1",
                    store_numbers=["R484"], stores=[], mode="instant", channels={})
    db.add(t)
    db.commit()
    admin_router.patch_user(
        u.id, admin_router.AdminUserPatchEx(paused_tasks=True), _req(), admin, db
    )
    db.refresh(t)
    assert t.paused is True
    assert t.paused_reason == "manual"


def test_claim_does_not_resume_manual_paused_tasks(db):
    """自动恢复是白名单口径：只有 quota_exhausted 会恢复，manual 不动。"""
    u = _user(db, tier="free", email="login@example.com")
    t = MonitorTask(user_id=None, device_id="dev-1", name="t", category="iphone",
                    part_number="P1", store_numbers=["R484"], stores=[], mode="instant",
                    channels={}, paused=True, paused_reason="manual")
    db.add(t)
    db.commit()
    info = auth_router._claim_device_tasks(db, _req({"x-device-id": "dev-1"}), u)
    db.refresh(t)
    assert info["claimed"] == 1
    assert info["resumed"] == 0
    assert t.paused is True  # manual 未被自动恢复


def test_claim_resumes_quota_exhausted_tasks(db):
    u = _user(db, tier="free", email="login2@example.com")
    t = MonitorTask(user_id=None, device_id="dev-2", name="t", category="iphone",
                    part_number="P1", store_numbers=["R484"], stores=[], mode="instant",
                    channels={}, paused=True, paused_reason="quota_exhausted")
    db.add(t)
    db.commit()
    info = auth_router._claim_device_tasks(db, _req({"x-device-id": "dev-2"}), u)
    db.refresh(t)
    assert info["resumed"] == 1
    assert t.paused is False


# ---------- R7-4：幂等——任务被删后同 key TTL 内走 409 ----------
def test_idempotency_deleted_task_not_treated_as_new_request(db):
    """首次结果的任务被删：_idempotent_replay 返回 None，且 key 占住记录的
    task_ids 非空 → _claim_idempotency_key 不接管（owned=False），上游走 409
    idempotency_in_progress，直到 10 分钟 TTL 过期。"""
    scope, key = "u:1", "k-deleted"
    rec, owned = tasks_router._claim_idempotency_key(db, scope, key)
    assert owned is True
    rec.task_ids = [999999]  # 模拟：首次创建的任务已被删除
    db.add(rec)
    db.commit()
    assert tasks_router._idempotent_replay(db, None, None, scope, key) is None
    rec2, owned2 = tasks_router._claim_idempotency_key(db, scope, key)
    assert owned2 is False  # TTL 内不接管 → 调用方 409，而非按新请求处理
