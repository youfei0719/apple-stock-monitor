"""round22 后端修复回归测试。

R22-P2-1  claim_payment 原子 UPDATE 追加 status 守卫——并发下另一管理员的
           close 已落库（resolved），本请求守卫读到旧 ORM 快照仍会通过；
           原子 UPDATE 必须把 status 条件带进 WHERE，否则"不予开通"的订单
           照样被 apply_tier_grant 开出会员。rowcount!=1 时重读 DB 真值区分
           bad_status（已流转）/ already_claimed（被先认领）。
R22-P3-2  task_expiry_sweep 不再给匿名 trial 任务写 task_expiring 通知行
           （匿名用户调不了 /notifications，永远不可见）。
"""

import os
import sys
import threading
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import Request
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.errors import APIError
from app.api.routers import admin as admin_router
from app.core.db import Base
from app.core.timeutil import utcnow
from app.models.models import MonitorTask, Notification, Payment, User
from app.services import lifecycle


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


def _user(db, tier="free", email="r22@example.com", days_left=None, **kw):
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


def _payment(db, status, tier_to="standard", order_id=None, user_id=None):
    p = Payment(
        order_id=order_id or f"r22-{status}-{id(db) % 100000}",
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


def _task(db, user_id=None, device_id=None, days_left=0.5):
    t = MonitorTask(
        user_id=user_id,
        device_id=device_id,
        name="r22-task",
        part_number="MJYC4CH/A",
        product_name="iPhone 18 Pro Max",
        color="银色",
        capacity="512GB",
        store_numbers=["R484"],
        stores=[{"number": "R484", "name": "益田假日广场", "city": "深圳"}],
        mode="instant",
        channels={},
        paused=False,
        expires_at=utcnow() + timedelta(days=days_left),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


# ---------- R22-P2-1：claim × close 并发 ----------
def test_claim_amount_mismatch_still_works(db):
    """新增 status 守卫不误伤正常认领：amount_mismatch 订单仍可认领。"""
    admin = _user(db, email="r22-admin@example.com", is_admin=True)
    u = _user(db, email="r22-buyer@example.com")
    p = _payment(db, "amount_mismatch", order_id="r22-mm-1")
    out = admin_router.claim_payment(
        p.id, admin_router.PaymentClaimIn(user_id=u.id), _req(), admin, db
    )
    assert out["ok"] is True
    assert out["payment_status"] == "resolved"
    db.refresh(u)
    assert u.tier == "standard"


def test_claim_vs_close_concurrent_close_wins(tmp_path):
    """双线程：A 认领 amount_mismatch 订单、B 同时关闭。

    A 先把订单读进自己会话的身份映射（旧快照），再等 B 的 close 落库；
    此时 A 的守卫看到旧状态会通过——旧代码原子 UPDATE 无 status 条件照样
    成功并开出会员（幽灵会员）。修复后必须走 400 bad_status，且会员不开。
    """
    engine = create_engine(
        f"sqlite:///{tmp_path}/r22.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    db0 = maker()
    admin_id = _user(db0, email="r22-a1@example.com", is_admin=True).id
    admin_b_id = _user(db0, email="r22-a2@example.com", is_admin=True).id
    buyer_id = _user(db0, email="r22-buyer@example.com").id
    pid = _payment(db0, "amount_mismatch", order_id="r22-claim-close-1").id
    db0.close()

    loaded = threading.Event()  # A 的旧快照已读入
    close_done = threading.Event()  # B 的 close 已落库
    results = {}

    def claim_thread():
        db = maker()
        try:
            # 注意：返回值必须用局部变量强引用住！Session 身份映射是弱引用，
            # 裸 db.get(...) 丢弃返回值会被 GC，导致 claim 时重新 SELECT 读到
            # B 已提交的新状态、守卫直接拦下——那样测不到并发窗口。生产代码里
            # p = db.get(...) 本来就被局部变量持有，这里如实复现。
            snap = db.get(Payment, pid)  # 旧快照：status=amount_mismatch
            assert snap.status == "amount_mismatch"
            loaded.set()
            assert close_done.wait(10), "close thread did not finish"
            admin_router.claim_payment(
                pid,
                admin_router.PaymentClaimIn(user_id=buyer_id),
                _req(),
                SimpleNamespace(id=admin_id),
                db,
            )
            results["claim"] = "ok"
        except APIError as e:
            results["claim"] = e.code
        finally:
            db.close()

    def close_thread():
        assert loaded.wait(10), "claim thread did not load snapshot"
        db = maker()
        try:
            admin_router.close_payment(
                pid, _req(), SimpleNamespace(id=admin_b_id), db
            )
            results["close"] = "ok"
        finally:
            db.close()
        close_done.set()

    ta = threading.Thread(target=claim_thread)
    tb = threading.Thread(target=close_thread)
    ta.start()
    tb.start()
    ta.join(15)
    tb.join(15)
    engine.dispose()

    assert results.get("close") == "ok", f"close failed: {results}"
    assert results.get("claim") == "bad_status", (
        f"旧快照绕过守卫的认领必须被原子 UPDATE 的 status 条件拦下，实际: {results}"
    )
    # 幽灵会员不能开出
    db1 = maker()
    try:
        assert db1.get(User, buyer_id).tier == "free"
        assert db1.get(Payment, pid).status == "resolved"
    finally:
        db1.close()
        engine.dispose()


# ---------- R22-P3-2：sweep 跳过匿名 trial 的 task_expiring ----------
def test_task_expiry_sweep_skips_anonymous_trial_reminder(db):
    u = _user(db, email="r22-sweep@example.com")
    anon = _task(db, user_id=None, device_id="r22-anon-1", days_left=0.5)
    mine = _task(db, user_id=u.id, days_left=0.5)
    stats = lifecycle.task_expiry_sweep(db)
    notes = (
        db.execute(
            select(Notification).where(Notification.kind == "task_expiring")
        )
        .scalars()
        .all()
    )
    got = {n.task_id for n in notes}
    assert mine.id in got  # 注册用户的提醒照常写
    assert anon.id not in got  # 匿名 trial 不写无人读取的通知行
    assert stats["reminders"] == 1
