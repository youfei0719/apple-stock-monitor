"""round9 后端修复回归测试。

R9-D2  claim_payment 原子认领（admin.py:431-440）：带条件的 UPDATE +
        rowcount 校验，并发双击只有一个能绑定成功，失败方 already_claimed，
        不二次开会员
R9-I14 三条恢复路径（pay.py:303 webhook / admin.py:449 admin claim /
        admin.py:302 admin 改档）：只恢复 paused_reason="tier_limit"，
        按新档位限 slots
R9-D5  overview active_tasks 口径（admin.py:105-114）：paused ∨
        expires_at IS NULL ∨ expires_at >= now，与 tasks._is_expired 对齐
R9-O2  batch_create 独立 IP 限流（tasks.py:449）：键 batch_create:{ip}
        10次/小时，与 task_create:{ip} 独立另计
R9-O3  未验证账号不泄露密码正确性（auth.py:348-363）：不存在→401；
        未验证→403（不论密码对错、不记登录失败）；已验证+密码错→401
R9-O4  sessions 清理：改密删其他 session（当前保留）、退出删当前 session
"""

import hashlib
import hmac
import json
import os
import sys
from datetime import timedelta

import pytest
from fastapi import Request, Response
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.errors import APIError
from app.api.routers import admin as admin_router
from app.api.routers import auth as auth_router
from app.api.routers import pay as pay_router
from app.api.routers import tasks as tasks_router
from app.core import ratelimit
from app.core.db import Base
from app.core.security import hash_password, new_session_token, token_digest
from app.core.timeutil import utcnow
from app.models.models import MonitorTask, Payment, User
from app.models.models import Session as DbSession
from app.schemas import (
    ChannelsIn,
    LoginIn,
    PasswordChangeIn,
    StoreIn,
    TaskBatchIn,
    TaskCreateIn,
)


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


def _user(db, tier="free", email="r9@example.com", days_left=None, **kw):
    exp = utcnow() + timedelta(days=days_left) if days_left is not None else None
    u = User(
        email=email,
        password_hash=kw.pop("password_hash", "x"),
        tier=tier,
        tier_expires_at=exp,
        quota_reset_at=utcnow() + timedelta(days=30),
        email_verified=kw.pop("email_verified", True),
        **kw,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _admin(db):
    a = _user(db, tier="pro", email="admin9@example.com")
    a.is_admin = True
    db.add(a)
    db.commit()
    return a


def _task(db, user_id, paused=False, paused_reason=None, expires_at=None, hours_ago=0):
    import uuid

    t = MonitorTask(
        user_id=user_id,
        name=f"t-{uuid.uuid4().hex[:8]}",
        part_number="PN",
        store_numbers=["R761"],
        paused=paused,
        paused_reason=paused_reason,
        expires_at=expires_at,
        updated_at=utcnow() - timedelta(hours=hours_ago),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _unclaimed_payment(db, tier_to="standard", order_id=None):
    p = Payment(
        order_id=order_id or f"r9-{id(db) % 100000}-{utcnow().timestamp()}",
        plan="standard_monthly",
        amount_cny=19.0,
        tier_from="free",
        tier_to=tier_to,
        status="paid",
        user_id=None,
        raw_payload={},
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ============ R9-D2：原子认领 ============
def test_claim_payment_atomic_update_rowcount(db):
    """直接验证原子 UPDATE 的 rowcount 语义：第一个请求占住（rowcount=1），
    第二个请求条件不再成立（rowcount=0）→ 走 already_claimed 分支。"""
    p = _unclaimed_payment(db)
    u1 = _user(db, email="r9d2a@example.com")
    u2 = _user(db, email="r9d2b@example.com")
    r1 = db.execute(
        update(Payment)
        .where(Payment.id == p.id, Payment.user_id.is_(None))
        .values(user_id=u1.id)
    ).rowcount
    assert r1 == 1
    r2 = db.execute(
        update(Payment)
        .where(Payment.id == p.id, Payment.user_id.is_(None))
        .values(user_id=u2.id)
    ).rowcount
    assert r2 == 0  # 并发第二个请求 rowcount=0 → already_claimed


def test_claim_payment_double_claim_no_double_grant(db):
    """第二次认领 400 already_claimed，且会员只开通一次（到期时间不变）。"""
    admin = _admin(db)
    u = _user(db, email="r9d2buyer@example.com")
    p = _unclaimed_payment(db)
    out = admin_router.claim_payment(
        p.id, admin_router.PaymentClaimIn(user_id=u.id), _req(), admin, db
    )
    assert out["ok"] is True
    db.refresh(u)
    assert u.tier == "standard"
    first_expires = u.tier_expires_at
    assert first_expires is not None

    u2 = _user(db, email="r9d2other@example.com")
    with pytest.raises(APIError) as exc:
        admin_router.claim_payment(
            p.id, admin_router.PaymentClaimIn(user_id=u2.id), _req(), admin, db
        )
    assert exc.value.status_code == 400
    assert exc.value.code == "already_claimed"
    db.refresh(u)
    assert u.tier_expires_at == first_expires  # 没有二次开会员


# ============ R9-I14：三条恢复路径 ============
def _tier_limited_setup(db, email, n_active=0, n_tier_limited=0, n_manual=0):
    """free 用户：n_active 个活跃 + n_tier_limited 个 tier_limit 暂停 +
    n_manual 个 manual 暂停。"""
    u = _user(db, tier="free", email=email)
    for i in range(n_active):
        _task(db, u.id, paused=False, hours_ago=i)
    for i in range(n_tier_limited):
        _task(db, u.id, paused=True, paused_reason="tier_limit", hours_ago=10 + i)
    for i in range(n_manual):
        _task(db, u.id, paused=True, paused_reason="manual", hours_ago=20 + i)
    return u


def _count(db, user_id, **kw):
    return (
        db.execute(select(MonitorTask).where(MonitorTask.user_id == user_id, **kw))
        .scalars()
        .all()
    )


def _webhook_grant(db, monkeypatch, user, plan_id="plan_std", amount=990, order_tag="w"):
    """走真实 afdian_webhook（pay.py:303 路径）：remark 带 user_id 关联用户。"""
    monkeypatch.setattr(pay_router.settings, "AFDIAN_TOKEN", "test-token")
    monkeypatch.setattr(pay_router.settings, "AFDIAN_PLAN_STANDARD", plan_id)
    payload = {
        "data": {
            "order": {
                "out_trade_no": f"OID-R9-I14-{order_tag}",
                "plan_id": plan_id,
                "total_amount": amount,
                "remark": str(user.id),
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
    return pay_router.afdian_webhook(req, db, raw)


def test_i14_webhook_grant_resumes_only_tier_limited_capped_by_slots(db, monkeypatch):
    """pay.py:303：升级成功后只恢复 tier_limit 暂停，且按新档位限 slots。"""
    u = _tier_limited_setup(
        db, "r9i14w@example.com", n_active=3, n_tier_limited=9, n_manual=2
    )
    out = _webhook_grant(db, monkeypatch, u)
    assert out["ok"] is True
    db.refresh(u)
    assert u.tier == "standard"
    # standard 上限 10，已有 3 活跃 → slots=7：恢复 7 个 tier_limit，
    # 剩下 2 个 tier_limit 继续暂停；manual 的 2 个不动
    resumed = [t for t in _count(db, u.id) if not t.paused]
    assert len(resumed) == 10
    still_paused_tier_limit = [
        t
        for t in _count(db, u.id)
        if t.paused and t.paused_reason == "tier_limit"
    ]
    assert len(still_paused_tier_limit) == 2
    still_manual = [
        t for t in _count(db, u.id) if t.paused and t.paused_reason == "manual"
    ]
    assert len(still_manual) == 2
    assert any("自动恢复 7 个" in n for n in out.get("notices", []))


def test_i14_claim_payment_resumes_tier_limited(db):
    """admin.py:449：管理员认领订单开通成功后恢复 tier_limit 暂停。"""
    admin = _admin(db)
    u = _tier_limited_setup(db, "r9i14c@example.com", n_active=2, n_tier_limited=3)
    p = _unclaimed_payment(db)
    out = admin_router.claim_payment(
        p.id, admin_router.PaymentClaimIn(user_id=u.id), _req(), admin, db
    )
    assert out["ok"] is True
    tasks = _count(db, u.id)
    assert all(not t.paused for t in tasks)  # 2 活跃 + 3 恢复 = 5 全活跃
    assert all(t.paused_reason is None for t in tasks)
    assert any("自动恢复 3 个" in n for n in out["notices"])


def test_i14_patch_user_upgrade_resumes_tier_limited(db):
    """admin.py:302：管理员手动改档升级后恢复 tier_limit 暂停。"""
    admin = _admin(db)
    u = _tier_limited_setup(db, "r9i14p@example.com", n_active=1, n_tier_limited=2)
    out = admin_router.patch_user(
        u.id, admin_router.AdminUserPatchEx(tier="standard"), _req(), admin, db
    )
    assert out["ok"] is True
    db.refresh(u)
    assert u.tier == "standard"
    tasks = _count(db, u.id)
    assert all(not t.paused for t in tasks)
    assert any("自动恢复 2 个" in n for n in out.get("notices", []))


def test_i14_no_resume_on_non_upgrade(db, monkeypatch):
    """非升级路径不恢复：金额异常订单落库（不 grant）时 tier_limit 任务保持暂停。"""
    u = _tier_limited_setup(db, "r9i14n@example.com", n_active=1, n_tier_limited=2)
    monkeypatch.setattr(pay_router.settings, "AFDIAN_TOKEN", "test-token")
    monkeypatch.setattr(pay_router.settings, "AFDIAN_PLAN_STANDARD", "plan_std")
    # 金额不符 → amount_mismatch 落库，不 grant
    payload = {
        "data": {
            "order": {
                "out_trade_no": "OID-R9-I14-MISMATCH",
                "plan_id": "plan_std",
                "total_amount": 1,
                "remark": str(u.id),
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
    out = pay_router.afdian_webhook(req, db, raw)
    assert out["status"] == "amount_mismatch"
    paused = [t for t in _count(db, u.id) if t.paused]
    assert len(paused) == 2  # 保持暂停，没有被恢复


# ============ R9-D5：overview 口径 ============
def test_overview_active_tasks_predicate(db):
    """admin.py:105-114 谓词：paused ∨ expires_at IS NULL ∨ expires_at >= now。

    注意含"已手动暂停"的任务（与旧 paused IS False 口径不同），
    与 tasks._is_expired 的德摩根形式一致。
    """
    admin = _admin(db)
    u = _user(db, email="r9d5@example.com")
    now = utcnow()
    _task(db, u.id, paused=False, expires_at=None)  # active
    _task(db, u.id, paused=False, expires_at=now + timedelta(days=1))  # active
    _task(db, u.id, paused=False, expires_at=now - timedelta(days=1))  # expired
    _task(db, u.id, paused=True, expires_at=now - timedelta(days=1))  # 手动暂停→active
    _task(db, u.id, paused=True, expires_at=None)  # 手动暂停→active
    out = admin_router.overview(admin, db)
    assert out["active_tasks"] == 4


# ============ R9-O2：batch 独立 IP 限流 ============
def _batch_data(i):
    return TaskBatchIn(
        part_numbers=[f"PN9{i}"],
        store_numbers=[f"R9{i:02d}"],
        channels=ChannelsIn(email="r9o2@example.com"),
    )


def test_batch_rate_limit_key_independent(db, monkeypatch):
    """batch 用 batch_create:{ip} 键、10次/小时，与 task_create:{ip} 独立。"""
    seen = {}

    def spy(key, limit=None, window_sec=None):
        seen["key"] = key
        seen["limit"] = limit
        seen["window_sec"] = window_sec
        return True

    monkeypatch.setattr(tasks_router, "check_rate_limit", spy)
    u = _user(db, tier="standard", email="r9o2a@example.com")
    tasks_router.batch_create(_batch_data(100), _req(ip="10.10.9.1"), u, db)
    assert seen["key"] == "batch_create:10.10.9.1"
    assert seen["limit"] == tasks_router.BATCH_CREATE_LIMIT == 10
    assert seen["window_sec"] == tasks_router.BATCH_CREATE_WINDOW_SEC == 3600


def test_batch_rate_limit_429_after_10(db):
    """真实限流器：同一 IP 10 次批量成功，第 11 次 429；且不影响单建桶。"""
    ip = "10.10.9.2"
    u = _user(db, tier="standard", email="r9o2b@example.com")
    for i in range(10):
        out = tasks_router.batch_create(_batch_data(i), _req(ip=ip), u, db)
        assert len(out) == 1
    with pytest.raises(APIError) as exc:
        tasks_router.batch_create(_batch_data(999), _req(ip=ip), u, db)
    assert exc.value.status_code == 429
    assert exc.value.code == "rate_limited"
    # 独立另计：同 IP 单建走 task_create:{ip} 桶，不应被批量桶限流。
    # 此时用户任务已达 standard 上限 10，单建应 403 task_limit 而非 429。
    data = TaskCreateIn(
        name="single",
        part_number="PN9S",
        stores=[StoreIn(number="R9S")],
        channels=ChannelsIn(email="r9o2@example.com"),
    )
    with pytest.raises(APIError) as exc2:
        tasks_router.create_task(data, _req(ip=ip), u, db)
    assert exc2.value.status_code == 403
    assert exc2.value.code == "task_limit"


# ============ R9-O3：未验证账号不泄露密码正确性 ============
def _login(db, email, password, ip):
    return auth_router.login(LoginIn(email=email, password=password), _req(ip=ip), Response(), db)


def test_o3_unverified_always_403_regardless_of_password(db):
    """未验证账号：密码对→403，密码错→403，不可区分；且不记登录失败。"""
    ip = "10.10.9.3"
    _user(
        db,
        email="r9o3@example.com",
        email_verified=False,
        password_hash=hash_password("correct-pw-123"),
    )
    with pytest.raises(APIError) as e1:
        _login(db, "r9o3@example.com", "correct-pw-123", ip)
    assert (e1.value.status_code, e1.value.code) == (403, "email_unverified")
    with pytest.raises(APIError) as e2:
        _login(db, "r9o3@example.com", "wrong-password", ip)
    assert (e2.value.status_code, e2.value.code) == (403, "email_unverified")
    # 未验证账号的密码不可用，不计入 IP 锁定
    assert ip not in ratelimit._login_state


def test_o3_nonexistent_401_and_verified_wrong_401(db):
    """不存在→401；已验证+密码错→401（401/403 状态码不可区分出"账号是否存在"）。"""
    ip = "10.10.9.4"
    with pytest.raises(APIError) as e1:
        _login(db, "nobody9@example.com", "whatever-pw", ip)
    assert (e1.value.status_code, e1.value.code) == (401, "bad_credentials")
    _user(
        db,
        email="r9o3v@example.com",
        email_verified=True,
        password_hash=hash_password("correct-pw-123"),
    )
    with pytest.raises(APIError) as e2:
        _login(db, "r9o3v@example.com", "wrong-password", ip)
    assert (e2.value.status_code, e2.value.code) == (401, "bad_credentials")


# ============ R9-O4：sessions 清理 ============
def _session(db, user_id, hours=1):
    tok = new_session_token()
    s = DbSession(
        user_id=user_id,
        token_digest=token_digest(tok),
        ip="1.2.3.4",
        expires_at=utcnow() + timedelta(hours=hours),
    )
    db.add(s)
    db.commit()
    return tok


def _user_sessions(db, user_id):
    return (
        db.execute(select(DbSession).where(DbSession.user_id == user_id)).scalars().all()
    )


def test_o4_change_password_deletes_other_sessions(db):
    """改密：删该用户其他 session，当前会话保留。"""
    u = _user(db, email="r9o4@example.com", password_hash=hash_password("old-pass-123"))
    tok_cur = _session(db, u.id)
    _session(db, u.id)  # 另一个会话：改密后应被删除
    auth_router.patch_me(
        PasswordChangeIn(old_password="old-pass-123", password="new-pass-456"),
        u,
        db,
        tok_cur,
    )
    remaining = _user_sessions(db, u.id)
    assert len(remaining) == 1
    assert remaining[0].token_digest == token_digest(tok_cur)


def test_o4_logout_deletes_current_session(db):
    """退出：删当前 session。"""
    u = _user(db, email="r9o4b@example.com")
    tok = _session(db, u.id)
    auth_router.logout(_req(), Response(), db, tok)
    assert _user_sessions(db, u.id) == []
