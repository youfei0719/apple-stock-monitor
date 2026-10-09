"""round24 后端修复回归测试。"""

import os
import sys
import threading
from datetime import timedelta

import pytest
from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.errors import APIError
from app.api.routers import tasks as tasks_router
from app.core.db import Base
from app.core.timeutil import utcnow
from app.models.models import MonitorTask, User
from app.schemas import TaskBatchIn, TaskCreateIn


@pytest.fixture
def engine(tmp_path):
    # 多线程共享：文件库 + check_same_thread=False（:memory: 每连接独立库，
    # 线程间看不到彼此的数据）
    e = create_engine(f"sqlite:///{tmp_path}/r24.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(e)
    yield e
    e.dispose()


def _req(headers=None, ip="10.24.24.24"):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(scope={"type": "http", "headers": raw, "client": (ip, 1234)})


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


def _user(db, tier="free", email="r24@example.com"):
    u = User(
        email=email,
        password_hash="x",
        tier=tier,
        tier_expires_at=utcnow() + timedelta(days=30),
        quota_reset_at=utcnow() + timedelta(days=30),
        email_verified=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ---------- R24-P2-1：同内容并发建任务 check-then-insert 竞态 ----------
def test_p2_1_concurrent_same_content_different_keys_single_row(engine):
    """双线程同内容、不同 Idempotency-Key 并发建任务：最终只有 1 行任务；
    输家拿到幂等返回（同一任务）或 409 idempotency_in_progress，不再建出
    重复行。内容键唯一约束做串行化，与时序无关，断言是确定性的。"""
    session_factory = sessionmaker(bind=engine)
    barrier = threading.Barrier(2)
    results = {}

    def worker(i):
        db = session_factory()
        try:
            barrier.wait(timeout=10)
            out = tasks_router.create_task(
                _task_in(),
                _req({"idempotency-key": f"r24-key-{i}"}),
                None,
                db,
                "dev-r24",
            )
            results[i] = ("ok", out.id)
        except APIError as e:
            results[i] = ("api_error", e.status_code, e.code)
        finally:
            db.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    db = session_factory()
    try:
        rows = db.query(MonitorTask).filter(MonitorTask.device_id == "dev-r24").all()
        assert len(rows) == 1, f"建出了重复任务：{len(rows)} 行"
        task_id = rows[0].id
        assert len(results) == 2
        for i, r in results.items():
            if r[0] == "ok":
                assert r[1] == task_id, f"线程 {i} 拿到了不同任务 {r[1]}"
            else:
                assert r == ("api_error", 409, "idempotency_in_progress"), f"线程 {i} 意外结果 {r}"
    finally:
        db.close()


def test_p2_1_same_content_sequential_returns_existing_task(engine):
    """R24-P2-1 语义变化：同内容重复提交（不同 key，串行）不再 409
    task_conflict，而是幂等返回已建任务，不新增行。
    （用 free 登录用户：trial 上限 1，配额检查在内容键之前，沿用原有 403 语义）"""
    session_factory = sessionmaker(bind=engine)
    db = session_factory()
    try:
        user = _user(db, email="r24seq@example.com")
        uid = user.id
        kw = dict(channels={"email": "a@b.c"})
        out1 = tasks_router.create_task(
            _task_in(**kw), _req({"idempotency-key": "r24-seq-a"}), user, db, None
        )
        # 同一 session 内 user 已持久化；新开 session 取避免跨调用状态干扰
        db.close()
        db = session_factory()
        user = db.get(User, uid)
        out2 = tasks_router.create_task(
            _task_in(**kw), _req({"idempotency-key": "r24-seq-b"}), user, db, None
        )
        assert out2.id == out1.id
        n = db.query(MonitorTask).filter(MonitorTask.user_id == uid).count()
        assert n == 1
        # 第三次同内容提交同样幂等返回（内容键 replay）
        out3 = tasks_router.create_task(
            _task_in(**kw), _req({"idempotency-key": "r24-seq-c"}), user, db, None
        )
        assert out3.id == out1.id
    finally:
        db.close()


def test_p2_1_concurrent_batch_same_content_single_rows(engine):
    """batch_create 同改：双线程同内容、不同 key 并发批量建任务，
    最终只有一批任务行；输家拿到幂等返回或 409 idempotency_in_progress。"""
    session_factory = sessionmaker(bind=engine)
    db = session_factory()
    try:
        uid = _user(db, email="r24batch@example.com").id
    finally:
        db.close()

    def batch_in():
        return TaskBatchIn(
            part_numbers=["MJYC4CH/A"],
            store_numbers=["R484", "R761"],
            category="iphone",
            mode="instant",
            channels={"email": "a@b.c"},
        )

    barrier = threading.Barrier(2)
    results = {}

    def worker(i):
        s = session_factory()
        try:
            user = s.get(User, uid)
            barrier.wait(timeout=10)
            outs = tasks_router.batch_create(
                batch_in(),
                _req({"idempotency-key": f"r24-bkey-{i}"}, ip="10.24.24.25"),
                user,
                s,
            )
            results[i] = ("ok", sorted(o.id for o in outs))
        except APIError as e:
            results[i] = ("api_error", e.status_code, e.code)
        finally:
            s.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    db = session_factory()
    try:
        rows = db.query(MonitorTask).filter(MonitorTask.user_id == uid).all()
        assert len(rows) == 2, f"批量建出了重复任务：{len(rows)} 行"
        ids = sorted(t.id for t in rows)
        assert len(results) == 2
        for i, r in results.items():
            if r[0] == "ok":
                assert r[1] == ids, f"线程 {i} 拿到了不同任务 {r[1]}"
            else:
                assert r == ("api_error", 409, "idempotency_in_progress"), f"线程 {i} 意外结果 {r}"
    finally:
        db.close()
