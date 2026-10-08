"""round13 后端修复回归测试。

R13-P2-5 升级/续费成功后，配额耗尽暂停的任务在新周期配额可用时自动恢复：
  - 只恢复 paused_reason == "quota_exhausted"（manual / tier_limit 等不动）
  - 新周期配额无余量时不动（避免恢复→立刻再暂停的抖动）
  - 按档位 tasks_limit 限额恢复
"""

import os
import sys
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import Base  # noqa: E402
from app.models.models import MonitorTask, QuotaUsage, User  # noqa: E402
from app.services.engine import quota_period_key  # noqa: E402
from app.services.lifecycle import resume_quota_exhausted_tasks  # noqa: E402


@pytest.fixture
def db():
    e = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e)
    s = sessionmaker(bind=e)()
    yield s
    s.close()


def _user(db, tier="pro"):
    u = User(
        email=f"r13q-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        tier=tier,
        tier_expires_at=datetime.utcnow() + timedelta(days=30),
        quota_reset_at=datetime.utcnow() + timedelta(days=30),
        email_verified=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _task(db, user, reason):
    t = MonitorTask(
        user_id=user.id,
        name=f"q-{reason}",
        part_number="MJYC4CH/A",
        store_numbers=["R484"],
        paused=True,
        paused_reason=reason,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _set_usage(db, user, push_count):
    row = QuotaUsage(
        user_id=user.id, period=quota_period_key(user), push_count=push_count
    )
    db.add(row)
    db.commit()


# ============ R13-P2-5：有余量时只恢复 quota_exhausted ============
def test_resume_quota_exhausted_only(db):
    u = _user(db)
    _set_usage(db, u, 10)  # pro 上限 500，有余量
    tq = _task(db, u, "quota_exhausted")
    tm = _task(db, u, "manual")
    tt = _task(db, u, "tier_limit")

    resumed = resume_quota_exhausted_tasks(db, u)
    db.commit()

    assert [t.id for t in resumed] == [tq.id]
    db.refresh(tq)
    db.refresh(tm)
    db.refresh(tt)
    assert tq.paused is False and tq.paused_reason is None
    assert tm.paused is True and tm.paused_reason == "manual"
    assert tt.paused is True and tt.paused_reason == "tier_limit"


# ============ R13-P2-5：配额无余量时不动 ============
def test_no_headroom_no_resume(db):
    u = _user(db)
    _set_usage(db, u, 500)  # pro 上限 500，已耗尽
    tq = _task(db, u, "quota_exhausted")

    resumed = resume_quota_exhausted_tasks(db, u)
    db.commit()

    assert resumed == []
    db.refresh(tq)
    assert tq.paused is True and tq.paused_reason == "quota_exhausted"


# ============ R13-P2-5：按 tasks_limit 限额恢复 ============
def test_resume_capped_by_tasks_limit(db):
    u = _user(db, tier="standard")  # tasks_limit=10, push_limit=100
    _set_usage(db, u, 0)
    # 先占满 10 个活跃任务
    for i in range(10):
        t = MonitorTask(
            user_id=u.id,
            name=f"active-{i}",
            part_number="MJYC4CH/A",
            store_numbers=["R484"],
            paused=False,
        )
        db.add(t)
    db.commit()
    tq = _task(db, u, "quota_exhausted")

    resumed = resume_quota_exhausted_tasks(db, u)
    db.commit()

    assert resumed == []
    db.refresh(tq)
    assert tq.paused is True
