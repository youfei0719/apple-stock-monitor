"""round2 后端修复回归测试：会员/配额/重试/反薅/支付口径。"""

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.errors import APIError
from app.api.routers import auth as auth_router
from app.api.routers import pay as pay_router
from app.core.db import Base
from app.core.tiers import effective_tier
from app.models.models import MonitorTask, Notification, QuotaUsage, SystemConfig, User
from app.services import engine as engine_mod
from app.services.engine import Engine, quota_period_key
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


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


def _user(db, tier="free", days_left=None, email="u@example.com"):
    exp = datetime.utcnow() + timedelta(days=days_left) if days_left is not None else None
    u = User(email=email, password_hash="x", tier=tier, tier_expires_at=exp)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _task(db, user=None, device_id="dev1", name="t1"):
    t = MonitorTask(
        user_id=user.id if user else None,
        device_id=device_id if not user else None,
        name=name,
        part_number="MJYC4CH/A",
        store_numbers=["R484"],
        stores=[{"number": "R484", "name": "益田", "city": "深圳"}],
        channels={"email": "u@example.com"},
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


class FakeNotifier:
    """可配置行为的 dispatch 替身：mode='sent' 全成功 / 'failed' 全失败。"""

    mode = "sent"

    def __init__(self, db):
        self.db = db

    def dispatch(self, user_id, task_id, channels, title, body, link, kind="stock_alert"):
        rows = []
        targets = []
        if isinstance(channels, dict):
            if channels.get("email"):
                targets.append(("email", channels["email"]))
            if channels.get("bark_key"):
                targets.append(("bark", channels["bark_key"]))
        for ch, target in targets or [("email", "t@example.com")]:
            n = Notification(
                user_id=user_id,
                task_id=task_id,
                kind=kind,
                channel=ch,
                target=target,
                title=title,
                body=body,
                link=link,
                status="sent" if self.mode == "sent" else "failed",
                error=None if self.mode == "sent" else "boom",
            )
            self.db.add(n)
            rows.append(n)
        self.db.flush()
        return rows


# ---------- 断裂-1：有效档位 ----------
def test_effective_tier_expired_standard_is_free(db):
    u = _user(db, tier="standard", days_left=-1)
    assert effective_tier(u) == "free"
    u2 = _user(db, tier="standard", days_left=5, email="u2@example.com")
    assert effective_tier(u2) == "standard"
    assert effective_tier(None) == "trial"


# ---------- 配额：锚点 / 只查不扣 / 按成功扣 ----------
def test_quota_anchor_init_and_check_does_not_consume(db, eng):
    u = _user(db, tier="standard", days_left=5)
    assert u.quota_reset_at is None
    t = _task(db, user=u)
    assert eng._check_quota(db, t) is True
    assert u.quota_reset_at is not None
    # 只检查不扣减：多次检查后 push_count 仍为 0
    eng._check_quota(db, t)
    usage = db.execute(
        select(QuotaUsage).where(
            QuotaUsage.user_id == u.id, QuotaUsage.period == quota_period_key(u)
        )
    ).scalar_one()
    assert usage.push_count == 0


def test_consume_quota_counts_sent_only(db, eng):
    u = _user(db, tier="standard", days_left=5)
    t = _task(db, user=u)
    eng._check_quota(db, t)
    eng._consume_quota(db, t, 0)  # 发送 0 条不扣
    eng._consume_quota(db, t, 3)  # 实际发送 3 条扣 3
    usage = db.execute(
        select(QuotaUsage).where(
            QuotaUsage.user_id == u.id, QuotaUsage.period == quota_period_key(u)
        )
    ).scalar_one()
    assert usage.push_count == 3


def test_quota_anchor_rolls_and_old_period_invalid(db, eng):
    u = _user(db, tier="free", days_left=5)
    # 锚点已过期 40 天：应滚动到未来，且老周期计数不再命中
    u.quota_reset_at = datetime.utcnow() - timedelta(days=40)
    db.add(u)
    db.commit()
    old_period = quota_period_key(u)
    db.add(QuotaUsage(user_id=u.id, period=old_period, push_count=5))
    db.commit()
    t = _task(db, user=u)
    assert eng._check_quota(db, t) is True  # free 上限 5，老周期 5 次已清零
    assert u.quota_reset_at > datetime.utcnow()
    assert quota_period_key(u) != old_period


def test_trial_quota_check_consume(db, eng):
    t = _task(db, user=None, device_id="devX")  # trial 上限 1
    assert eng._check_quota(db, t) is True
    eng._consume_quota(db, t, 1)
    assert eng._check_quota(db, t) is False


# ---------- 断裂-5：_fire 按成功扣减 + 快照 + 重试 ----------
def test_fire_counts_sent_and_snapshots_part_number(db, eng, monkeypatch):
    monkeypatch.setattr(engine_mod, "Notifier", FakeNotifier)
    FakeNotifier.mode = "sent"
    u = _user(db, tier="standard", days_left=5)
    t = _task(db, user=u)
    sent = eng._fire(db, t, "R484", "MJYC4CH/A", None)
    assert sent == 1
    n = db.execute(select(Notification)).scalar_one()
    assert n.part_number == "MJYC4CH/A"
    assert n.status == "sent"
    assert t.consecutive_failures == 0


def test_fire_immediate_retry_success_counts_once(db, eng, monkeypatch):
    monkeypatch.setattr(engine_mod, "Notifier", FakeNotifier)
    FakeNotifier.mode = "failed"
    monkeypatch.setattr(Engine, "_resend_record", lambda self, n: True)
    u = _user(db, tier="standard", days_left=5)
    t = _task(db, user=u)
    sent = eng._fire(db, t, "R484", "MJYC4CH/A", None)
    assert sent == 1  # 立即重试成功计 1 次，不重复扣
    n = db.execute(select(Notification)).scalar_one()
    assert n.status == "sent"
    assert n.retry_count == 1
    assert n.retry_at is None


def test_fire_all_failed_increments_and_autopauses_at_10(db, eng, monkeypatch):
    monkeypatch.setattr(engine_mod, "Notifier", FakeNotifier)
    FakeNotifier.mode = "failed"
    monkeypatch.setattr(Engine, "_resend_record", lambda self, n: False)
    u = _user(db, tier="standard", days_left=5)
    t = _task(db, user=u)
    t.consecutive_failures = 9
    db.add(t)
    db.commit()
    sent = eng._fire(db, t, "R484", "MJYC4CH/A", None)
    assert sent == 0
    db.refresh(t)
    assert t.consecutive_failures == 10
    assert t.paused is True
    kinds = [r.kind for r in db.execute(select(Notification)).scalars().all()]
    assert "task_auto_paused" in kinds
    n = db.execute(
        select(Notification).where(Notification.kind == "stock_alert")
    ).scalar_one()
    assert n.retry_count == 1
    assert n.retry_at is not None  # 仍失败：记 retry_at 等后续 tick 重发


def test_retry_pending_notifications(db, eng, monkeypatch):
    monkeypatch.setattr(Engine, "_resend_record", lambda self, n: True)
    u = _user(db, tier="free", days_left=5)
    n = Notification(
        user_id=u.id,
        task_id=None,
        kind="stock_alert",
        channel="email",
        target="u@example.com",
        title="t",
        body="b",
        link="",
        status="failed",
        error="boom",
        retry_count=1,
        retry_at=datetime.utcnow() - timedelta(seconds=60),
    )
    db.add(n)
    db.commit()
    eng._retry_pending_notifications(db)
    db.refresh(n)
    assert n.status == "sent"
    assert n.retry_count == 2
    assert n.retry_at is None


# ---------- 断裂-4：配额预警去重 ----------
def test_quota_warning_dedupe(db, eng, monkeypatch):
    monkeypatch.setattr(engine_mod, "Notifier", FakeNotifier)
    FakeNotifier.mode = "sent"
    u = _user(db, tier="free", days_left=5)  # free 上限 5
    t = _task(db, user=u)
    eng._check_quota(db, t)  # 初始化锚点（同时创建 usage 行）
    usage = db.execute(
        select(QuotaUsage).where(
            QuotaUsage.user_id == u.id, QuotaUsage.period == quota_period_key(u)
        )
    ).scalar_one()
    usage.push_count = 4
    db.add(usage)
    db.commit()
    eng._maybe_quota_warning(db, t)
    eng._maybe_quota_warning(db, t)  # 重复调用不重复发
    rows = db.execute(
        select(Notification).where(Notification.kind == "quota_warning")
    ).scalars().all()
    assert len(rows) == 1


# ---------- 断裂-20：请求预算 ----------
def test_apply_budgets_per_user_limit_skips(db, eng):
    u = _user(db, tier="free", days_left=5)
    t = _task(db, user=u)
    t.store_numbers = [f"R{i}" for i in range(40)]  # est=4 > trial? free 上限 10
    db.add(t)
    db.commit()
    # trial 任务：est=4 > trial 上限 3 → 跳过
    t2 = _task(db, user=None, device_id="devY", name="t2")
    t2.store_numbers = [f"R{i}" for i in range(40)]
    db.add(t2)
    db.commit()
    kept = eng._apply_budgets(db, [t, t2], __import__("time").time())
    assert t in kept  # free 上限 10，est=4 通过
    assert t2 not in kept  # trial 上限 3，est=4 被跳过
    skipped = db.execute(
        select(Notification).where(Notification.error == "per_user_rate_limit")
    ).scalars().all()
    assert len(skipped) == 1


def test_peak_mode_flag(db, eng):
    assert eng._peak_mode(db) is False
    db.add(SystemConfig(key="peak_mode", value={"enabled": True}))
    db.commit()
    assert eng._peak_mode(db) is True


# ---------- 支付：升级/降级/续费口径 ----------
def test_apply_tier_grant_upgrade_immediate(db):
    u = _user(db, tier="free", days_left=None)
    assert pay_router.apply_tier_grant(db, u, "standard") == "granted"
    assert u.tier == "standard"
    assert u.pending_tier is None
    assert u.quota_reset_at > datetime.utcnow() + timedelta(days=29)


def test_apply_tier_grant_downgrade_pending(db):
    u = _user(db, tier="pro", days_left=20)
    old_exp = u.tier_expires_at
    assert pay_router.apply_tier_grant(db, u, "standard") == "downgrade_pending"
    assert u.tier == "pro"  # 到期前保持
    assert u.pending_tier == "standard"
    assert u.tier_expires_at == old_exp  # 到期时间不动


def test_apply_tier_grant_renew_extends_from_old_expiry(db):
    u = _user(db, tier="standard", days_left=20)
    old_exp = u.tier_expires_at
    assert pay_router.apply_tier_grant(db, u, "standard") == "granted"
    assert u.tier_expires_at > old_exp  # 提前续费不亏天数


def test_resolve_user_by_remark(db):
    u = _user(db, email="buyer@example.com")
    assert pay_router._resolve_user(db, str(u.id), "").id == u.id
    assert pay_router._resolve_user(db, "buyer@example.com", "").id == u.id
    assert pay_router._resolve_user(db, "nobody", "") is None


# ---------- 断裂-10：登录/注册迁移设备任务 ----------
class _FakeRequest:
    def __init__(self, device_id):
        self.headers = {"x-device-id": device_id} if device_id else {}


def test_claim_device_tasks(db):
    t = _task(db, user=None, device_id="devZ")
    u = _user(db, email="new@example.com")
    n = auth_router._claim_device_tasks(db, _FakeRequest("devZ"), u)
    assert n == 1
    db.commit()  # 真实流程中 register/login 随后 commit
    db.refresh(t)
    assert t.user_id == u.id
    # 无 device 头：不迁移
    t2 = _task(db, user=None, device_id="devW", name="t2")
    assert auth_router._claim_device_tasks(db, _FakeRequest(None), u) == 0
    db.refresh(t2)
    assert t2.user_id is None


# ---------- 断裂-22：邮箱验证 ----------
def test_verify_email_flow(db):
    u = _user(db, email="v@example.com")
    assert u.email_verified is False
    db.add(
        SystemConfig(
            key="email_code:v@example.com",
            value={
                "code": "123456",
                "expires_at": (datetime.utcnow() + timedelta(minutes=10)).isoformat() + "Z",
            },
        )
    )
    db.commit()
    # 错误验证码
    bad = auth_router.VerifyEmailIn(email="v@example.com", code="000000")
    with pytest.raises(APIError) as ei:
        auth_router.verify_email(bad, db)
    assert ei.value.code == "bad_code"
    # 正确验证码
    good = auth_router.VerifyEmailIn(email="v@example.com", code="123456")
    out = auth_router.verify_email(good, db)
    assert out["ok"] is True
    db.refresh(u)
    assert u.email_verified is True
    # 验证码行已删除
    row = db.execute(
        select(SystemConfig).where(SystemConfig.key == "email_code:v@example.com")
    ).scalar_one_or_none()
    assert row is None
