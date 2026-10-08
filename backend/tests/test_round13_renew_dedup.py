"""round13 后端修复回归测试。

R13-P1-1 一键续期去重改用 last_renewed_at 专用列：
  - 引擎 poll（写 last_polled_at/last_poll_ok/last_poll_ms，会推进
    updated_at）之后再点续期，不得被误判为重复点击跳过
  - 真正的 10 秒内重复点击仍被去重（expires_at 不再叠加）
"""

import os
import sys
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.routers import tasks as tasks_router  # noqa: E402
from app.core.db import Base  # noqa: E402
from app.models.models import MonitorTask, User  # noqa: E402


@pytest.fixture
def db():
    e = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e)
    s = sessionmaker(bind=e)()
    yield s
    s.close()


def _user(db):
    u = User(
        email=f"r13-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        tier="pro",
        tier_expires_at=datetime.utcnow() + timedelta(days=30),
        quota_reset_at=datetime.utcnow() + timedelta(days=30),
        email_verified=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _task(db, user, days_left=5):
    t = MonitorTask(
        user_id=user.id,
        name="续期去重回归",
        part_number="MJYC4CH/A",
        store_numbers=["R484"],
        expires_at=datetime.utcnow() + timedelta(days=days_left),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _engine_poll(db, task):
    """模拟引擎一轮 poll：写 last_polled_at 等字段并提交（updated_at 被 onupdate 推进）。"""
    task.last_polled_at = datetime.utcnow()
    task.last_poll_ok = True
    task.last_poll_ms = 12.5
    db.add(task)
    db.commit()
    db.refresh(task)


# ============ R13-P1-1：引擎 poll 后续期不得被误判跳过 ============
def test_renew_not_deduped_after_engine_poll(db):
    u = _user(db)
    t = _task(db, u, days_left=5)
    old_exp = t.expires_at
    old_updated = t.updated_at
    # 引擎 poll 紧贴着发生（updated_at 被推到 ~now，旧逻辑下 10s 窗口内必误判）
    _engine_poll(db, t)
    assert t.updated_at >= old_updated
    assert (datetime.utcnow() - t.updated_at).total_seconds() < 10

    out = tasks_router.renew_task(t.id, u, db, None)

    # 续期必须真实生效：expires_at = max(now, old) + 30d
    assert out.expires_at is not None
    gained = (out.expires_at.replace(tzinfo=None) - old_exp).total_seconds()
    assert 29 * 86400 < gained <= 30 * 86400 + 60
    # 去重列被写入
    db.refresh(t)
    assert t.last_renewed_at is not None


# ============ R13-P1-1：真双击仍被去重 ============
def test_renew_double_click_still_deduped(db):
    u = _user(db)
    t = _task(db, u, days_left=5)
    first = tasks_router.renew_task(t.id, u, db, None)
    second = tasks_router.renew_task(t.id, u, db, None)
    # 第二次命中去重：expires_at 不再叠加
    assert second.expires_at == first.expires_at
    db.refresh(t)
    first_exp = first.expires_at.replace(tzinfo=None)
    assert abs((t.expires_at - first_exp).total_seconds()) < 1


# ============ R13-P1-1：去重窗口过后可再次续期 ============
def test_renew_after_window_renews_again(db):
    u = _user(db)
    t = _task(db, u, days_left=5)
    first = tasks_router.renew_task(t.id, u, db, None)
    # 把去重列拨到窗口之外
    t.last_renewed_at = datetime.utcnow() - timedelta(
        seconds=tasks_router.RENEW_DEDUP_WINDOW_SEC + 1
    )
    db.add(t)
    db.commit()
    second = tasks_router.renew_task(t.id, u, db, None)
    first_exp = first.expires_at.replace(tzinfo=None)
    second_exp = second.expires_at.replace(tzinfo=None)
    assert (second_exp - first_exp).total_seconds() > 29 * 86400
