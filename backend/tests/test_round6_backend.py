"""round6 后端修复回归测试。"""

import os
import sys
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import Request
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.errors import APIError
from app.api.routers import auth as auth_router
from app.api.routers import pay as pay_router
from app.api.routers import tasks as tasks_router
from app.core.db import Base
from app.models.models import IdempotencyRecord, MonitorTask, Notification, StockState, User
from app.schemas import TaskCreateIn, TaskPatchIn
from app.services import engine as engine_mod
from app.services import lifecycle as lifecycle_mod
from app.services.apple_client import AppleRateLimitError
from app.services.engine import Engine


@pytest.fixture
def db():
    e = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e)
    s = sessionmaker(bind=e)()
    yield s
    s.close()


@pytest.fixture
def eng():
    return Engine()


def _req(headers=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(scope={"type": "http", "headers": raw, "client": ("9.9.9.9", 1234)})


def _user(db, tier="free", email="r6@example.com", days_left=None):
    exp = datetime.utcnow() + timedelta(days=days_left) if days_left is not None else None
    u = User(
        email=email,
        password_hash="x",
        tier=tier,
        tier_expires_at=exp,
        quota_reset_at=datetime.utcnow() + timedelta(days=30),
        email_verified=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _task_in(**kw):
    base = dict(
        name="t1",
        category="iphone",
        part_number="MJYC4CH/A",
        stores=[{"number": "R484", "name": "益田", "city": "深圳"}],
        channels={},
    )
    base.update(kw)
    return TaskCreateIn(**base)


# ---------- R6-D2：并发 grant 原子性 ----------
def test_d2_concurrent_grants_stack_to_60_days(tmp_path):
    """两线程同时续费：barrier 保证都读到 stale 状态再写。

    旧 read-modify-write 实现下两者都算出 now+30（lost-update，最终 30 天）；
    原子 UPDATE 下最终 60 天。
    """
    url = f"sqlite:///{tmp_path}/d2.db"
    e = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(e)
    mk = sessionmaker(bind=e)
    s = mk()
    s.add(User(email="d2@example.com", password_hash="x", tier="free", email_verified=True))
    s.commit()
    uid = s.execute(select(User.id)).scalar_one()
    s.close()

    barrier = threading.Barrier(2)

    def grant():
        ss = mk()
        try:
            user = ss.get(User, uid)
            barrier.wait(timeout=10)
            pay_router.apply_tier_grant(ss, user, "standard")
            ss.commit()
        finally:
            ss.close()

    ts = [threading.Thread(target=grant) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    s = mk()
    u = s.get(User, uid)
    days = (u.tier_expires_at - datetime.utcnow()).days
    assert u.tier == "standard"
    assert days >= 59, f"lost-update：两次续费只剩 {days} 天"
    assert u.quota_reset_at == u.tier_expires_at
    s.close()


def test_d2_sequential_grants_stack(db):
    u = _user(db, tier="free", email="d2s@example.com")
    pay_router.apply_tier_grant(db, u, "standard")
    db.commit()
    pay_router.apply_tier_grant(db, u, "standard")
    db.commit()
    db.refresh(u)
    days = (u.tier_expires_at - datetime.utcnow()).days
    assert days >= 59


# ---------- R6-D3：bootstrap 远未来到期 + sweep 跳过管理员 ----------
def test_d3_bootstrap_admin_far_future_expiry(db, monkeypatch):
    import app.main as main_mod

    monkeypatch.setattr(main_mod, "SessionLocal", sessionmaker(bind=db.get_bind()))
    monkeypatch.setattr(main_mod.settings, "ADMIN_EMAIL", "boss@example.com")
    monkeypatch.setattr(main_mod.settings, "ADMIN_PASSWORD", "secret123")
    main_mod._bootstrap_admin()
    admin = db.execute(select(User).where(User.is_admin.is_(True))).scalar_one()
    assert admin.tier == "pro"
    assert admin.tier_expires_at is not None
    assert (admin.tier_expires_at - datetime.utcnow()).days > 3000


def test_d3_membership_sweep_skips_admin(db):
    admin = _user(db, tier="pro", email="r6admin@example.com", days_left=-1)
    admin.is_admin = True
    db.add(admin)
    db.commit()
    stats = lifecycle_mod.membership_sweep(db)
    db.refresh(admin)
    assert admin.tier == "pro", "管理员不应被 sweep 降级"
    assert stats["downgraded"] == 0


def test_d3_membership_sweep_still_downgrades_normal_users(db):
    u = _user(db, tier="pro", email="r6normal@example.com", days_left=-1)
    stats = lifecycle_mod.membership_sweep(db)
    db.refresh(u)
    assert u.tier == "free"
    assert stats["downgraded"] == 1


# ---------- R6-D4：空渠道 400（创建/PATCH），trial 豁免 ----------
def test_d4_create_empty_channels_400_for_paid(db):
    u = _user(db, tier="free", email="d4@example.com")
    with pytest.raises(APIError) as ei:
        tasks_router.create_task(_task_in(), _req(), u, db, None)
    assert ei.value.status_code == 400
    assert ei.value.code == "channels_required"


def test_d4_create_empty_channels_ok_for_trial(db):
    out = tasks_router.create_task(_task_in(), _req(), None, db, "dev-d4")
    assert out.id is not None


def test_d4_patch_clear_channels_400(db):
    u = _user(db, tier="free", email="d4p@example.com")
    out = tasks_router.create_task(
        _task_in(channels={"email": "a@b.c"}), _req(), u, db, None
    )
    with pytest.raises(APIError) as ei:
        tasks_router.patch_task(out.id, TaskPatchIn(channels={}), u, db, None)
    assert ei.value.status_code == 400
    assert ei.value.code == "channels_required"


def test_d4_engine_zero_channels_skipped_and_no_event_advance(db, eng, monkeypatch):
    """零渠道付费任务到货：记 skipped 行，不推进 last_event_at（边沿不消费）。"""

    class EmptyNotifier:
        def __init__(self, db):
            self.db = db

        def dispatch(
            self, user_id, task_id, channels, title, body, link, kind="stock_alert", commit=True
        ):
            return []

    monkeypatch.setattr(engine_mod, "Notifier", EmptyNotifier)
    u = _user(db, tier="free", email="d4e@example.com")
    t = MonitorTask(
        user_id=u.id,
        name="t",
        part_number="MJYC4CH/A",
        store_numbers=["R484"],
        stores=[{"number": "R484", "name": "益田", "city": "深圳"}],
        channels={},
    )
    db.add(t)
    db.commit()
    row = StockState(
        task_id=t.id, store_number="R484", part_number="MJYC4CH/A", prev_known="unavailable"
    )
    db.add(row)
    db.commit()
    r = SimpleNamespace(
        state="available", pickup_display="", store_pick_eligible=True, pickup_search_quote=""
    )
    eng._process_task(db, t, ("MJYC4CH/A",), ["R484"], {("R484", "MJYC4CH/A"): r})
    db.refresh(row)
    skipped = db.execute(
        select(Notification).where(Notification.channel == "no_channel")
    ).scalar_one_or_none()
    assert skipped is not None and skipped.status == "skipped"
    assert row.last_event_at is None, "零渠道时 last_event_at 不应推进"


# ---------- R6-D1：限流后同 tick 其余分组不再打 Apple ----------
def test_d1_poll_group_returns_true_on_rate_limit(db, eng, monkeypatch):
    u = _user(db, tier="free", email="d1@example.com")
    t = MonitorTask(
        user_id=u.id,
        name="t",
        part_number="MJYC4CH/A",
        store_numbers=["R484"],
        stores=[{"number": "R484", "name": "益田", "city": "深圳"}],
        channels={"email": "a@b.c"},
    )
    db.add(t)
    db.commit()

    def fake_query(parts, stores):
        raise AppleRateLimitError("429 rate limited")

    monkeypatch.setattr(eng.client, "query", fake_query)
    limited = eng._poll_group(db, "shenzhen", ("MJYC4CH/A",), [t])
    assert limited is True
    assert eng._in_cooldown(db) is True


# ---------- R6-I4：认领剥离渠道密钥 ----------
def test_i4_claim_strips_channel_secrets(db):
    t = MonitorTask(
        user_id=None,
        device_id="dev-claim",
        name="t",
        part_number="MJYC4CH/A",
        store_numbers=["R484"],
        stores=[{"number": "R484"}],
        channels={
            "bark_key": "secret123",
            "email": "x@y.z",
            "webhooks": [{"platform": "wecom", "url": "https://hooks.example/secret"}],
        },
    )
    db.add(t)
    db.commit()
    u = _user(db, tier="free", email="claim@example.com")
    info = auth_router._claim_device_tasks(db, _req({"x-device-id": "dev-claim"}), u)
    db.commit()
    db.refresh(t)
    assert t.user_id == u.id
    assert t.device_id is None
    assert t.channels.get("bark_key") is None
    assert t.channels.get("email") is None
    assert t.channels.get("webhooks") == []
    assert info["claimed"] == 1
    assert info["secrets_stripped"] == 1
    assert "重新配置" in (auth_router._claim_notice(info) or "")


# ---------- R6-I9：认领恢复配额耗尽暂停的任务 ----------
def test_i9_claim_resumes_quota_exhausted_only(db):
    def _anon(paused, reason):
        t = MonitorTask(
            user_id=None,
            device_id="dev-resume",
            name=f"t-{reason}",
            part_number="MJYC4CH/A",
            store_numbers=["R484"],
            stores=[{"number": "R484"}],
            channels={},
            paused=paused,
            paused_reason=reason,
        )
        db.add(t)
        return t

    t_quota = _anon(True, "quota_exhausted")
    t_manual = _anon(True, "manual")
    t_zombie = _anon(True, "zombie")
    db.commit()
    u = _user(db, tier="free", email="resume@example.com")
    info = auth_router._claim_device_tasks(db, _req({"x-device-id": "dev-resume"}), u)
    db.commit()
    for t in (t_quota, t_manual, t_zombie):
        db.refresh(t)
    assert t_quota.paused is False and t_quota.paused_reason is None
    assert t_manual.paused is True and t_manual.paused_reason == "manual"
    assert t_zombie.paused is True and t_zombie.paused_reason == "zombie"
    assert info["resumed"] == 1


# ---------- R6-P2-12：过去 expires_at 400 ----------
def test_p2_12_past_expires_at_400(db):
    u = _user(db, tier="free", email="d12@example.com")
    past = datetime.utcnow() - timedelta(hours=1)
    with pytest.raises(APIError) as ei:
        tasks_router.create_task(
            _task_in(expires_at=past, channels={"email": "a@b.c"}), _req(), u, db, None
        )
    assert ei.value.status_code == 400
    assert ei.value.code == "bad_expires_at"


# ---------- R6-P2-10：幂等键 ----------
def test_p2_10_idempotency_key_replays_first_result(db):
    headers = {"idempotency-key": "key-abc"}
    out1 = tasks_router.create_task(
        # 后-D-2：匿名 trial 仅支持站内，email 渠道不再放行；本用例测幂等回放，
        # 用空渠道（trial 允许）保持用例意图不变
        _task_in(channels={}), _req(headers), None, db, "dev-idem"
    )
    # 同 key 不同 payload：直接返回首次结果，不建新任务（trial 上限 1 也不 403）
    out2 = tasks_router.create_task(
        _task_in(name="other", channels={}), _req(headers), None, db, "dev-idem"
    )
    assert out2.id == out1.id
    assert (
        db.query(MonitorTask).filter(MonitorTask.device_id == "dev-idem").count() == 1
    )
    rec = db.execute(
        select(IdempotencyRecord).where(IdempotencyRecord.key == "key-abc")
    ).scalar_one()
    assert rec.task_ids == [out1.id]
    assert rec.scope == "d:dev-idem"
