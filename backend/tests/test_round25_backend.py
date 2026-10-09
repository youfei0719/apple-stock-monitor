"""round25 后端修复回归测试（P2-1 删任务后幂等记录清理；P3-1 batch 冲突释放内容键占位）。"""

import os
import sys
from datetime import timedelta

import pytest
from fastapi import Request
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.errors import APIError
from app.api.routers import tasks as tasks_router
from app.core.db import Base
from app.core.timeutil import utcnow
from app.models.models import IdempotencyRecord, MonitorTask, User
from app.schemas import TaskBatchIn, TaskCreateIn
from app.services.lifecycle import purge_task_idempotency_records


@pytest.fixture
def engine(tmp_path):
    e = create_engine(f"sqlite:///{tmp_path}/r25.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(e)
    yield e
    e.dispose()


def _req(headers=None, ip="10.25.25.25"):
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


def _batch_in(**kw):
    base = dict(
        part_numbers=["MJYC4CH/A"],
        store_numbers=["R484"],
        category="iphone",
        mode="instant",
        channels={"email": "a@b.c"},
    )
    base.update(kw)
    return TaskBatchIn(**base)


def _user(db, email):
    u = User(
        email=email,
        password_hash="x",
        tier="free",
        tier_expires_at=utcnow() + timedelta(days=30),
        quota_reset_at=utcnow() + timedelta(days=30),
        email_verified=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ---------- R25-P2-1：删任务后同内容立即重建不再 409 ----------
def test_p2_1_delete_then_recreate_same_content_201(engine):
    """创建→删除→同内容重建（不同 key）→ 201 成功，不再 409
    idempotency_in_progress（R24 引入的回归；用户删任务改渠道/改名后重建常用流）。"""
    sf = sessionmaker(bind=engine)
    db = sf()
    try:
        uid = _user(db, "r25del@example.com").id
        out1 = tasks_router.create_task(
            _task_in(channels={"email": "a@b.c"}),
            _req({"idempotency-key": "r25-key-a"}),
            db.get(User, uid),
            db,
            None,
        )
        old_id = out1.id
        tasks_router.delete_task(old_id, db.get(User, uid), db, None)
        assert db.query(MonitorTask).filter(MonitorTask.user_id == uid).count() == 0
        # 旧记录（内容键+用户键）已清理，不再指向已删任务
        stale = [
            r
            for r in db.execute(
                select(IdempotencyRecord).where(IdempotencyRecord.scope == f"u:{uid}")
            )
            .scalars()
            .all()
            if r.task_ids and old_id in r.task_ids
        ]
        assert stale == [], f"仍有幂等记录指向已删任务：{[(r.key, r.task_ids) for r in stale]}"
        # 同内容、不同 key 重建 → 201（无异常即成功；SQLite 无 AUTOINCREMENT
        # 时删光后 id 会复用，不以 id 不等断言新旧）
        out2 = tasks_router.create_task(
            _task_in(channels={"email": "a@b.c"}),
            _req({"idempotency-key": "r25-key-b"}),
            db.get(User, uid),
            db,
            None,
        )
        assert db.query(MonitorTask).filter(MonitorTask.user_id == uid).count() == 1
        # 内容键已重新指向当前任务行（而非已删旧行）
        rec = db.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.scope == f"u:{uid}",
                IdempotencyRecord.key == tasks_router._content_idempotency_key(
                    ["MJYC4CH/A", "R484"]
                ),
            )
        ).scalar_one()
        assert rec.task_ids == [out2.id]
        assert db.get(MonitorTask, out2.id) is not None
    finally:
        db.close()


def test_p2_1_delete_then_same_key_recreates_fresh(engine):
    """删任务后用同一个 Idempotency-Key 重提 → 按新请求创建（201），
    不再 replay 已删任务、也不 409。"""
    sf = sessionmaker(bind=engine)
    db = sf()
    try:
        uid = _user(db, "r25samekey@example.com").id
        out1 = tasks_router.create_task(
            _task_in(channels={"email": "a@b.c"}),
            _req({"idempotency-key": "r25-key-same"}),
            db.get(User, uid),
            db,
            None,
        )
        tasks_router.delete_task(out1.id, db.get(User, uid), db, None)
        # 无异常返回即 201 新建（SQLite id 可能复用，不以 id 不等断言）；
        # 关键是：用户键记录已指向当前任务行，不再 replay 已删任务
        out2 = tasks_router.create_task(
            _task_in(channels={"email": "a@b.c"}),
            _req({"idempotency-key": "r25-key-same"}),
            db.get(User, uid),
            db,
            None,
        )
        rec = db.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.scope == f"u:{uid}", IdempotencyRecord.key == "r25-key-same"
            )
        ).scalar_one()
        assert rec.task_ids == [out2.id]
        assert db.get(MonitorTask, out2.id) is not None
    finally:
        db.close()


def test_p2_1_purge_keeps_inflight_placeholder(engine):
    """purge 只删 task_ids 含被删任务 id 的记录；在途占位（task_ids 为空）
    与其他任务的记录不受影响。"""
    sf = sessionmaker(bind=engine)
    db = sf()
    try:
        scope = "u:999"
        inflight = IdempotencyRecord(scope=scope, key="content:inflight", task_ids=[])
        other = IdempotencyRecord(scope=scope, key="content:other", task_ids=[4242])
        db.add_all([inflight, other])
        db.commit()
        n = purge_task_idempotency_records(db, scope, 4242)
        assert n == 1
        assert db.get(IdempotencyRecord, inflight.id) is not None, "在途占位不应被删"
        assert db.get(IdempotencyRecord, other.id) is None
        # 其他 scope 的记录不受影响
        foreign = IdempotencyRecord(scope="u:1000", key="content:x", task_ids=[4242])
        db.add(foreign)
        db.commit()
        purge_task_idempotency_records(db, scope, 4242)
        assert db.get(IdempotencyRecord, foreign.id) is not None
    finally:
        db.close()


# ---------- R25-P3-1：batch 冲突 409 前释放内容键占位 ----------
def test_p3_1_batch_conflict_releases_content_key(engine):
    """批量撞已有任务 → 409 task_conflict；同内容修正/重试（不同 key，
    内容键相同）→ 再次 409 task_conflict（带具体冲突信息），
    不再 409 idempotency_in_progress。"""
    sf = sessionmaker(bind=engine)
    db = sf()
    try:
        uid = _user(db, "r25batch@example.com").id
        user = db.get(User, uid)
        tasks_router.create_task(
            _task_in(channels={"email": "a@b.c"}),
            _req({"idempotency-key": "r25-b-pre"}),
            user,
            db,
            None,
        )
        user = db.get(User, uid)
        with pytest.raises(APIError) as e1:
            tasks_router.batch_create(
                _batch_in(), _req({"idempotency-key": "r25-bk-1"}), user, db
            )
        assert (e1.value.status_code, e1.value.code) == (409, "task_conflict")
        # 同内容重试（不同 key；channels 不在内容键里，改个模板/渠道模拟"修正后重试"）
        user = db.get(User, uid)
        with pytest.raises(APIError) as e2:
            tasks_router.batch_create(
                _batch_in(name_template="新模板 {part_number} × {store_number}"),
                _req({"idempotency-key": "r25-bk-2"}),
                user,
                db,
            )
        assert (e2.value.status_code, e2.value.code) == (409, "task_conflict"), (
            f"重试应再次拿到 task_conflict，实际 {e2.value.status_code} {e2.value.code}"
        )
        # 无残留空占位
        empties = [
            r
            for r in db.execute(
                select(IdempotencyRecord).where(IdempotencyRecord.scope == f"u:{uid}")
            )
            .scalars()
            .all()
            if r.key.startswith("content:") and not r.task_ids
        ]
        assert empties == [], f"内容键占位未释放：{[r.key for r in empties]}"
    finally:
        db.close()


def test_p3_1_batch_internal_duplicate_releases_content_key(engine):
    """批量内重复组合 → 409 task_conflict；同内容重试 → 再次 409
    task_conflict，不再 409 idempotency_in_progress。"""
    sf = sessionmaker(bind=engine)
    db = sf()
    try:
        uid = _user(db, "r25batchdup@example.com").id
        user = db.get(User, uid)
        with pytest.raises(APIError) as e1:
            tasks_router.batch_create(
                _batch_in(store_numbers=["R484", "R484"]),
                _req({"idempotency-key": "r25-bd-1"}),
                user,
                db,
            )
        assert (e1.value.status_code, e1.value.code) == (409, "task_conflict")
        user = db.get(User, uid)
        with pytest.raises(APIError) as e2:
            tasks_router.batch_create(
                _batch_in(store_numbers=["R484", "R484"]),
                _req({"idempotency-key": "r25-bd-2"}),
                user,
                db,
            )
        assert (e2.value.status_code, e2.value.code) == (409, "task_conflict"), (
            f"重试应再次拿到 task_conflict，实际 {e2.value.status_code} {e2.value.code}"
        )
    finally:
        db.close()
