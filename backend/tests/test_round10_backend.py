"""round10 后端修复回归测试。

R10-P1-1 admin patch_user 手动降档做任务数收敛（converge_task_limit，
        reason="tier_limit"），暂停数写入 notices/changes；降为 free 时
        tier_expires_at 被清空，membership_sweep 永不扫该用户，收敛必须
        在这里做
R10-P1-2 tasks batch_create 三处 N+1：冲突检测单次查询后内存比对；
        返回前 selectinload 预加载 states；_idempotent_replay 同样预加载
R10-P2-1 pay webhook total_amount 非数字 → 按 amount_mismatch 落库待人工，
        不 500（爱发电会无限重试）
R10-P2-2 engine._trial_quota_state 匿名配额改北京时间月界
R10-P2-3 lifecycle._pruned_today/_mark_pruned 改北京时间口径
R10-P2-6 auth._claim_device_tasks 认领后冲突检测：超档位上限时收敛暂停
R10-P2-7 tasks.renew_task 并发双击去重：短窗口内重复点击不叠加 +30 天
R10-I5 admin GET /users 加 offset 真分页（limit/max 200 配合，响应仍为裸 list）
R10-I6 admin overview 返回 effective_tier 会员分布 + 有效付费会员数
"""

import asyncio
import hashlib
import hmac
import json
import os
import sys
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import Request
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.errors import APIError
from app.api.routers import admin as admin_router
from app.api.routers import auth as auth_router
from app.api.routers import pay as pay_router
from app.api.routers import tasks as tasks_router
from app.core.db import Base
from app.models.models import MonitorTask, Payment, SystemConfig, User
from app.schemas import ChannelsIn, TaskBatchIn


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
        email=email or f"r10-{uuid.uuid4().hex[:8]}@example.com",
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


def _admin(db):
    a = _user(db, tier="pro")
    a.is_admin = True
    db.add(a)
    db.commit()
    return a


def _task(db, user_id, paused=False, paused_reason=None, expires_at=None, **kw):
    t = MonitorTask(
        user_id=user_id,
        name=f"t-{uuid.uuid4().hex[:8]}",
        part_number=kw.pop("part_number", "PN10"),
        store_numbers=kw.pop("store_numbers", ["R761"]),
        paused=paused,
        paused_reason=paused_reason,
        expires_at=expires_at,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


# ============ R10-P1-1：手动降档收敛 ============
def test_patch_user_downgrade_to_free_converges(db):
    admin = _admin(db)
    u = _user(db, tier="pro", days_left=10)
    for _ in range(5):
        _task(db, u.id)
    out = admin_router.patch_user(
        u.id, admin_router.AdminUserPatchEx(tier="free"), _req(), admin, db
    )
    db.refresh(u)
    assert u.tier == "free"
    assert u.tier_expires_at is None  # 降为 free 清空到期时间
    tasks = db.execute(
        select(MonitorTask).where(MonitorTask.user_id == u.id)
    ).scalars().all()
    paused = [t for t in tasks if t.paused]
    active = [t for t in tasks if not t.paused]
    assert len(active) == 3 and len(paused) == 2  # free 上限 3
    assert all(t.paused_reason == "tier_limit" for t in paused)
    assert out["changes"]["tasks_paused"] == 2
    assert any("已暂停" in n for n in out["notices"])


def test_patch_user_downgrade_pro_to_standard_converges(db):
    admin = _admin(db)
    u = _user(db, tier="pro", days_left=10)
    for _ in range(12):
        _task(db, u.id)
    out = admin_router.patch_user(
        u.id, admin_router.AdminUserPatchEx(tier="standard"), _req(), admin, db
    )
    tasks = db.execute(
        select(MonitorTask).where(MonitorTask.user_id == u.id)
    ).scalars().all()
    assert sum(1 for t in tasks if not t.paused) == 10  # standard 上限 10
    assert out["changes"]["tasks_paused"] == 2


def test_patch_user_no_tier_change_no_converge(db):
    """只改其他字段时不触发收敛（changes 里无 tasks_paused）。"""
    admin = _admin(db)
    u = _user(db, tier="pro", days_left=10)
    for _ in range(5):
        _task(db, u.id)
    out = admin_router.patch_user(
        u.id, admin_router.AdminUserPatchEx(email_verified=True), _req(), admin, db
    )
    assert "tasks_paused" not in out["changes"]
    tasks = db.execute(
        select(MonitorTask).where(MonitorTask.user_id == u.id)
    ).scalars().all()
    assert all(not t.paused for t in tasks)


# ============ R10-P1-2：batch N+1 修复后行为一致 ============
def _batch(user_tag, i):
    return TaskBatchIn(
        part_numbers=[f"PNB{user_tag}{i}"],
        store_numbers=[f"RB{i:02d}"],
        channels=ChannelsIn(email="r10@example.com"),
    )


def test_batch_create_still_detects_conflicts(db):
    """单次查询+内存比对后，查重语义不变：批量内重复→409，与已有任务冲突→409。"""
    u = _user(db, tier="standard")
    # 与已有任务冲突
    _task(db, u.id, part_number="PNX", store_numbers=["R99"])
    dup = TaskBatchIn(
        part_numbers=["pnx"],  # 大小写归一化后仍应命中
        store_numbers=["r99"],
        channels=ChannelsIn(email="r10@example.com"),
    )
    with pytest.raises(APIError) as e1:
        tasks_router.batch_create(dup, _req(ip="10.10.10.1"), u, db)
    assert e1.value.code == "task_conflict"
    # 批量内重复
    dup2 = TaskBatchIn(
        part_numbers=["PNA", "PNA"],
        store_numbers=["R01"],
        channels=ChannelsIn(email="r10@example.com"),
    )
    with pytest.raises(APIError) as e2:
        tasks_router.batch_create(dup2, _req(ip="10.10.10.1"), u, db)
    assert e2.value.code == "task_conflict"


def test_batch_create_output_correct_with_preloaded_states(db):
    """预加载后返回的 TaskOut 内容正确（含 latest 结构）。"""
    u = _user(db, tier="standard")
    out = tasks_router.batch_create(_batch("A", 1), _req(ip="10.10.10.2"), u, db)
    assert len(out) == 1
    assert out[0].part_number == "PNBA1"
    assert out[0].latest["total"] == 0  # 新任务无 states 行


def test_idempotent_replay_returns_same_tasks(db):
    """幂等重放走预加载路径：同 key 第二次返回首次创建的任务。"""
    u = _user(db, tier="standard")
    headers = {"idempotency-key": "r10-replay-1"}
    out1 = tasks_router.batch_create(_batch("B", 1), _req(ip="10.10.10.3", headers=headers), u, db)
    out2 = tasks_router.batch_create(_batch("B", 2), _req(ip="10.10.10.3", headers=headers), u, db)
    assert [t.id for t in out2] == [t.id for t in out1]
    # 数据库里仍然只有 1 个任务（没有重复创建）
    n = db.execute(
        select(MonitorTask).where(MonitorTask.user_id == u.id)
    ).scalars().all()
    assert len(n) == 1


# ============ R10-P2-1：金额解析失败不 500 ============
def test_webhook_unparsable_amount_recorded_not_500(db, monkeypatch):
    """total_amount 非数字 → 不抛 500（爱发电会无限重试），按
    amount_mismatch 落库待人工，返回 200。"""
    monkeypatch.setattr(pay_router.settings, "AFDIAN_TOKEN", "test-token")
    monkeypatch.setattr(pay_router.settings, "AFDIAN_PLAN_STANDARD", "plan_std")
    payload = {
        "data": {
            "order": {
                "out_trade_no": "OID-R10-P21",
                "plan_id": "plan_std",
                "total_amount": "abc",
                "remark": "",
            }
        }
    }
    raw = json.dumps(payload).encode()
    sig = hmac.new(b"test-token", raw, hashlib.sha256).hexdigest()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    req = Request(
        scope={
            "type": "http",
            "headers": [(b"x-afdian-signature", sig.encode())],
            "client": ("9.9.9.9", 1234),
        },
        receive=receive,
    )
    out = asyncio.run(pay_router.afdian_webhook(req, db))
    assert out["ok"] is True
    assert out["status"] == "amount_mismatch"
    p = db.execute(
        select(Payment).where(Payment.order_id == "OID-R10-P21")
    ).scalar_one()
    assert p.status == "amount_mismatch"
    assert p.amount_cny == 0


# ============ R10-P2-2：trial 配额北京时间月界 ============
def test_trial_quota_state_beijing_month_boundary(db, monkeypatch):
    """UTC 2026-10-31 20:00（北京 11-01 04:00）：月界应为 2026-11，
    UTC 口径会是 2026-10（错月）。"""
    from types import SimpleNamespace

    from app.services import engine as engine_mod

    real_dt = datetime

    class FakeDT(real_dt):
        @classmethod
        def utcnow(cls):
            return real_dt(2026, 10, 31, 20, 0, 0)

    monkeypatch.setattr(engine_mod, "datetime", FakeDT)
    eng = engine_mod.Engine()
    _used, _limit, key = eng._trial_quota_state(db, SimpleNamespace(device_id="dev10"))
    assert key == "trial_quota:dev10:2026-11"


# ============ R10-P2-3：prune 北京时间口径 ============
def test_pruned_today_beijing_date(db, monkeypatch):
    """UTC 2026-10-31 20:00 落库的日期应为北京时间 2026-11-01，
    且 _pruned_today 同口径命中。"""
    from app.services import lifecycle as lc

    real_dt = datetime

    class FakeDT(real_dt):
        @classmethod
        def utcnow(cls):
            return real_dt(2026, 10, 31, 20, 0, 0)

    monkeypatch.setattr(lc, "datetime", FakeDT)
    assert lc._pruned_today(db, "k10") is False
    lc._mark_pruned(db, "k10")
    assert lc._pruned_today(db, "k10") is True
    row = db.execute(
        select(SystemConfig).where(SystemConfig.key == "k10")
    ).scalar_one()
    assert row.value["date"] == "2026-11-01"


# ============ R10-P2-6：认领冲突检测 ============
def test_claim_device_tasks_over_limit_converges(db):
    """free 用户已有 3 个任务（满上限）时认领匿名任务 → 总数 4，
    超限的 1 个被暂停（tier_limit），notice 提示用户。"""
    u = _user(db, tier="free")
    for _ in range(3):
        _task(db, u.id)
    anon = MonitorTask(
        user_id=None,
        device_id="dev10",
        name=f"anon-{uuid.uuid4().hex[:8]}",
        part_number="PN10",
        store_numbers=["R761"],
        channels={},
    )
    db.add(anon)
    db.commit()
    info = auth_router._claim_device_tasks(
        db, _req(headers={"x-device-id": "dev10"}), u
    )
    assert info["claimed"] == 1
    assert info["paused_over_limit"] == 1
    tasks = db.execute(
        select(MonitorTask).where(MonitorTask.user_id == u.id)
    ).scalars().all()
    assert sum(1 for t in tasks if not t.paused) == 3
    paused = [t for t in tasks if t.paused]
    assert len(paused) == 1 and paused[0].paused_reason == "tier_limit"
    notice = auth_router._claim_notice(info)
    assert "超出当前档位上限" in notice


def test_claim_device_tasks_within_limit_no_pause(db):
    """未超限时不暂停（paused_over_limit=0，notice 无超限提示）。"""
    u = _user(db, tier="free")
    _task(db, u.id)
    anon = MonitorTask(
        user_id=None,
        device_id="dev11",
        name=f"anon-{uuid.uuid4().hex[:8]}",
        part_number="PN10",
        store_numbers=["R761"],
        channels={},
    )
    db.add(anon)
    db.commit()
    info = auth_router._claim_device_tasks(
        db, _req(headers={"x-device-id": "dev11"}), u
    )
    assert info["claimed"] == 1
    assert info["paused_over_limit"] == 0
    tasks = db.execute(
        select(MonitorTask).where(MonitorTask.user_id == u.id)
    ).scalars().all()
    assert all(not t.paused for t in tasks)


# ============ R10-I5：users 真分页 ============
def test_list_users_offset_pagination(db):
    admin = _admin(db)
    for i in range(5):
        _user(db, email=f"r10page{i}@example.com")
    # 直接调用时 Query 参数按既有测试约定显式传（q/tier 不传会拿到 Query 对象）
    kw = dict(q=None, tier=None, admin=admin, db=db)
    page1 = admin_router.list_users(limit=2, offset=0, **kw)
    page2 = admin_router.list_users(limit=2, offset=2, **kw)
    page3 = admin_router.list_users(limit=2, offset=4, **kw)
    assert len(page1) == 2 and len(page2) == 2 and len(page3) == 2
    ids = [r["id"] for r in page1] + [r["id"] for r in page2] + [r["id"] for r in page3]
    assert len(set(ids)) == 6  # admin + 5 用户，不重不漏
    # 默认 offset=0 时行为不变（向后兼容）
    default_page = admin_router.list_users(limit=2, **kw)
    assert [r["id"] for r in default_page] == [r["id"] for r in page1]


# ============ R10-I6：effective_tier 统计 ============
def test_overview_effective_tier_matches_python(db):
    """overview 的 effective_tier_distribution 与逐用户调
    tiers.effective_tier 的结果一致（SQL CASE 口径对齐 Python 实现）。"""
    from app.core.tiers import effective_tier

    admin = _admin(db)  # pro，无到期时间 → 有效 pro
    _user(db, tier="pro", days_left=-1)  # 已到期 → 有效 free
    _user(db, tier="pro", days_left=10)  # 有效 pro
    _user(db, tier="standard", days_left=-2)  # 已到期 → 有效 free
    _user(db, tier="standard", days_left=None)  # 无到期时间 → 保持 standard
    _user(db, tier="free")
    _user(db, tier="trial")
    out = admin_router.overview(admin, db)
    users = db.execute(select(User)).scalars().all()
    expected: dict = {}
    for u in users:
        t = effective_tier(u)
        expected[t] = expected.get(t, 0) + 1
    assert out["effective_tier_distribution"] == expected
    assert expected == {"pro": 2, "free": 3, "standard": 1, "trial": 1}
    assert out["paid_members"] == 3  # 有效 standard(1) + pro(2)
    # 原始分布保留（向后兼容）：已到期的 2 个仍在 raw 的 standard/pro 里
    assert out["tier_distribution"]["pro"] == 3
    assert out["tier_distribution"]["standard"] == 2


# ============ R10-P2-7：续期双击去重 ============
def test_renew_double_click_no_stack(db):
    """10 秒去重窗口内第二次续期直接返回当前状态，不再叠加 +30 天。"""
    u = _user(db, tier="standard")
    t = _task(db, u.id, expires_at=datetime.utcnow() + timedelta(days=5))
    # 把 updated_at 回拨 1 分钟，绕过去重窗口，让第一次续期正常延长
    #（刚创建/编辑 10 秒内的续期本来就会被去重，这是预期的保守行为）
    t.updated_at = datetime.utcnow() - timedelta(minutes=1)
    db.add(t)
    db.commit()
    orig_expires = t.expires_at
    out1 = tasks_router.renew_task(t.id, u, db)
    exp1 = out1.expires_at
    assert exp1 > orig_expires  # 第一次确实延长了
    out2 = tasks_router.renew_task(t.id, u, db)  # 立即第二次（模拟双击）
    assert out2.expires_at == exp1  # 未叠加
