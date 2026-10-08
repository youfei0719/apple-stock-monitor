"""F-4 回归测试（用户拍板 A）：trial 站内触达按次扣减配额。

- trial 档 dispatch 时 page 通道触达记 sent（体验 1 次/月真实生效）；
- 外部渠道仍记 skipped；sent 条数只计 page，不多扣；
- 匿名 GET /quota 返回 trial 配额态（Home trialExhausted 横幅用）。
"""

import os
import sys
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.errors import APIError
from app.api.routers.quota import get_quota
from app.core.db import Base
from app.core.timeutil import utcnow
from app.models.models import MonitorTask, User
from app.services.engine import Engine
from app.services.notifier import Notifier


@pytest.fixture
def db():
    e = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e)
    s = sessionmaker(bind=e)()
    yield s
    s.close()


def _trial_task(db, device_id="dev-f4", channels=None, name="trial-t1"):
    t = MonitorTask(
        user_id=None,
        device_id=device_id,
        name=name,
        part_number="MJYC4CH/A",
        store_numbers=["R484"],
        stores=[{"number": "R484", "name": "益田", "city": "深圳"}],
        channels=channels or {},
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_trial_dispatch_records_page_sent(db):
    """匿名 trial 无外部渠道：dispatch 只记一条 page/sent。"""
    t = _trial_task(db)
    records = Notifier(db).dispatch(None, t.id, {}, "有货", "body", "http://x")
    assert len(records) == 1
    assert records[0].channel == "page"
    assert records[0].status == "sent"


def test_trial_dispatch_bark_skipped_but_page_sent_once(db):
    """trial 配了外部渠道：bark 记 skipped 备查，page 记 sent；sent 只计 1 次。"""
    t = _trial_task(db, channels={"bark_key": "testkey"})
    records = Notifier(db).dispatch(None, t.id, t.channels, "有货", "body", "http://x")
    by_channel = {r.channel: r.status for r in records}
    assert by_channel["bark"] == "skipped"
    assert by_channel["page"] == "sent"
    sent = sum(1 for r in records if r.status == "sent")
    assert sent == 1  # 配额只扣 1 次，不多扣


def test_trial_quota_consumed_and_exhausted(db):
    """page 触达计 sent 后走 _consume_quota：体验 1 次/月真实生效，第二次被拦。"""
    eng = Engine()
    t = _trial_task(db)
    assert eng._check_trial_quota(db, t) is True
    eng._consume_quota(db, t, 1)  # 模拟 _fire 返回 sent=1
    assert eng._check_trial_quota(db, t) is False  # 耗尽：下次触发走暂停分支


def test_quota_endpoint_anonymous(db):
    """匿名 GET /quota：有 X-Device-Id 返回 trial 配额态；无则 401。"""
    _trial_task(db, device_id="dev-f4")
    q = get_quota(user=None, db=db, x_device_id="dev-f4")
    assert q["tier"] == "trial"
    assert q["push_limit"] == 1
    assert q["push_used"] == 0
    assert q["tasks_limit"] == 1
    assert q["tasks_used"] == 1
    with pytest.raises(APIError) as exc:
        get_quota(user=None, db=db, x_device_id=None)
    assert exc.value.status_code == 401


def test_quota_endpoint_anonymous_reflects_consumed(db):
    """匿名配额接口的 push_used 跟随 trial 扣减（横幅判定依据）。"""
    eng = Engine()
    t = _trial_task(db, device_id="dev-f4b")
    eng._consume_quota(db, t, 1)
    q = get_quota(user=None, db=db, x_device_id="dev-f4b")
    assert q["push_used"] == 1
    assert q["push_used"] >= q["push_limit"]  # Home trialExhausted 横幅点亮条件


def test_quota_endpoint_logged_in_regression(db):
    """登录用户 /quota 行为不变（防匿名分支回归）。"""
    u = User(
        email="f4@example.com",
        password_hash="x",
        tier="free",
        quota_reset_at=utcnow() + timedelta(days=30),
        email_verified=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    q = get_quota(user=u, db=db, x_device_id="dev-f4")
    assert q["tier"] == "free"
    assert q["push_limit"] == 5
