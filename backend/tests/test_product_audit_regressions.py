"""用户创建配置与库存检查时间的回归测试。"""

import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import Request
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.routers import tasks as tasks_router
from app.core.db import Base
from app.core.timeutil import utcnow
from app.models.models import IdempotencyRecord, MonitorTask, StockState, User
from app.schemas import TaskBatchIn
from app.services import engine as engine_module


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/product-audit.db",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.mark.parametrize("tier,interval,expected", [("free", 60, 60), ("pro", None, 300)])
def test_batch_persists_complete_config_and_replays(db, tier, interval, expected):
    user = User(
        email=f"audit-{tier}@example.com",
        password_hash="x",
        tier=tier,
        email_verified=True,
        tier_expires_at=utcnow() + timedelta(days=30),
    )
    db.add(user)
    db.commit()
    request = Request(
        scope={
            "type": "http",
            "client": (f"10.26.0.{1 if tier == 'free' else 2}", 1234),
            "headers": [(b"idempotency-key", b"complete-config")],
        }
    )
    payload = TaskBatchIn(
        part_numbers=["MJYC4CH/A"],
        store_numbers=["R484", "R359"],
        group="购买目标",
        mode="confirmed",
        **({"repeat_interval_sec": interval} if interval is not None else {}),
    )
    first = tasks_router.batch_create(payload, request, user, db)
    assert len(first) == 2
    assert all(t.repeat_interval_sec == expected and t.group == "购买目标" for t in first)
    assert all(t.channels["email"] == user.email for t in first)
    assert all(t.product_name == "iPhone 18 Pro Max" for t in first)
    assert all(t.capacity == "512GB" and t.color == "银色" for t in first)
    assert all(t.stores[0]["name"] and t.stores[0]["city"] for t in first)
    assert all(t.creation_status == "created" for t in first)
    ids = [t.id for t in first]
    assert [t.id for t in tasks_router.batch_create(payload, request, user, db)] == ids
    assert all(
        t.creation_status == "existing"
        for t in tasks_router.batch_create(payload, request, user, db)
    )
    assert db.query(MonitorTask).count() == 2
    records = db.scalars(select(IdempotencyRecord)).all()
    assert records and all(r.task_ids == ids for r in records)


def test_batch_rejects_too_short_interval():
    with pytest.raises(ValidationError):
        TaskBatchIn(part_numbers=["MJYC4CH/A"], store_numbers=["R484"], repeat_interval_sec=59)


@pytest.mark.parametrize("failed", [False, True])
def test_identical_poll_results_still_advance_check_time(db, monkeypatch, failed):
    task = MonitorTask(
        name="时间测试",
        device_id="audit",
        part_number="MJYC4CH/A",
        store_numbers=["R484"],
        stores=[],
        mode="instant",
    )
    db.add(task)
    db.commit()
    state = "unknown" if failed else "unavailable"
    old_time = datetime(2026, 10, 10, 1)
    check_time = old_time + timedelta(minutes=5)
    db.add(
        StockState(
            task_id=task.id,
            store_number="R484",
            part_number=task.part_number,
            state=state,
            prev_known="unavailable",
            confirmed_count=0,
            updated_at=old_time,
        )
    )
    db.commit()
    monkeypatch.setattr(engine_module, "utcnow", lambda: check_time)
    poller = engine_module.Engine()
    if failed:
        poller._mark_unknown(db, task, (task.part_number,), ["R484"])
    else:
        result = SimpleNamespace(
            state=state, pickup_display=None, store_pick_eligible=None, pickup_search_quote=None
        )
        poller._process_task(
            db, task, (task.part_number,), ["R484"], {("R484", task.part_number): result}
        )
    db.commit()
    row = db.scalars(select(StockState)).one()
    assert row.updated_at == check_time
    assert row.state == state
    assert row.prev_known == "unavailable"


def test_explicit_null_disables_pro_repeat_and_patch_clears_it(db):
    from app.schemas import TaskPatchIn

    user = User(email="repeat-off@example.com", password_hash="x", tier="pro", email_verified=True)
    db.add(user)
    db.commit()
    request = Request(scope={"type": "http", "client": ("10.26.0.3", 1234), "headers": []})
    payload = TaskBatchIn(
        part_numbers=["MJYC4CH/A"], store_numbers=["R484"], repeat_interval_sec=None
    )
    [created] = tasks_router.batch_create(payload, request, user, db)
    assert created.repeat_interval_sec is None
    tasks_router.patch_task(created.id, TaskPatchIn(repeat_interval_sec=60), user, db, None)
    disabled = tasks_router.patch_task(
        created.id, TaskPatchIn(repeat_interval_sec=None), user, db, None
    )
    assert disabled.repeat_interval_sec is None
    untouched = tasks_router.patch_task(created.id, TaskPatchIn(name="重命名"), user, db, None)
    assert untouched.repeat_interval_sec is None


def test_store_edit_removes_old_results_and_invalidates_idempotency(db):
    from app.schemas import TaskPatchIn

    user = User(email="store-edit@example.com", password_hash="x", tier="free", email_verified=True)
    db.add(user)
    db.commit()
    request = Request(scope={"type": "http", "client": ("10.26.0.4", 1234), "headers": []})
    [created] = tasks_router.batch_create(
        TaskBatchIn(part_numbers=["MJYC4CH/A"], store_numbers=["R484"]), request, user, db
    )
    task = db.get(MonitorTask, created.id)
    task.last_polled_at = utcnow()
    task.last_poll_ok = True
    db.add(
        StockState(
            task_id=created.id,
            store_number="R484",
            part_number=task.part_number,
            state="available",
            prev_known="available",
            confirmed_count=2,
        )
    )
    db.commit()
    result = tasks_router.patch_task(
        created.id,
        TaskPatchIn(
            stores=[{"number": " r359 ", "name": "南京东路"}],
            name="新的购买目标",
            mode="confirmed",
            repeat_interval_sec=120,
        ),
        user,
        db,
        None,
    )
    assert result.stores[0]["number"] == "R359"
    assert result.name == "新的购买目标" and result.repeat_interval_sec == 120
    assert result.last_polled_at is None and result.last_poll_ok is None
    assert not db.scalars(select(StockState)).all()
    assert not db.scalars(select(IdempotencyRecord)).all()
    # 原范围可立即创建，不会重放已改范围的任务。
    [replacement] = tasks_router.batch_create(
        TaskBatchIn(part_numbers=["MJYC4CH/A"], store_numbers=["R484"]), request, user, db
    )
    assert replacement.id != created.id


def test_duplicate_store_edit_does_not_change_channels(db):
    from app.api.errors import APIError
    from app.schemas import TaskPatchIn

    task = MonitorTask(
        name="原配置",
        device_id="edit",
        part_number="MJYC4CH/A",
        stores=[],
        store_numbers=["R484"],
        channels={},
        mode="instant",
    )
    db.add(task)
    db.commit()
    with pytest.raises(APIError) as error:
        tasks_router.patch_task(
            task.id,
            TaskPatchIn(
                stores=[{"number": "R359"}, {"number": "r359"}],
                channels={"email": "changed@example.com"},
            ),
            None,
            db,
            "edit",
        )
    assert error.value.code == "bad_stores"
    assert task.channels == {} and task.store_numbers == ["R484"]


def test_task_health_uses_effective_tier(db):
    user = User(
        email="expired-pro@example.com",
        password_hash="x",
        tier="pro",
        tier_expires_at=utcnow() - timedelta(days=1),
    )
    task = MonitorTask(
        user=user,
        name="过期会员",
        part_number="MJYC4CH/A",
        stores=[],
        store_numbers=["R484"],
        mode="instant",
        last_polled_at=utcnow(),
        last_poll_ok=False,
        paused=True,
        paused_reason="quota_exhausted",
    )
    db.add(task)
    db.commit()
    result = tasks_router._task_out(task, db)
    assert result.refresh_interval_sec == engine_module.engine.settings.tier_intervals["free"]
    assert result.last_poll_ok is False and result.paused_reason == "quota_exhausted"
    assert result.model_dump(mode="json")["last_polled_at"].endswith("Z")


def test_global_rate_limit_returns_retry_after(monkeypatch):
    from app import main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main, "check_rate_limit", lambda ip: False)
    monkeypatch.setattr(main, "rate_limit_retry_after", lambda ip: 17)
    client = TestClient(main.app)
    response = client.get("/api/catalog/products")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "17"
    assert response.json()["code"] == "rate_limited"


def test_password_reset_consumes_code_and_revokes_all_sessions(db, monkeypatch):
    import re

    from app.api.errors import APIError
    from app.api.routers import auth
    from app.core.security import hash_password, verify_password
    from app.models.models import Session as DbSession

    monkeypatch.setattr(auth, "_pad_resend_timing", lambda start: None)
    sent = []
    monkeypatch.setattr(auth, "send_email", lambda email, title, body: sent.append(body))
    user = User(
        email="reset@example.com",
        password_hash=hash_password("OldAudit123"),
        tier="free",
        email_verified=True,
    )
    db.add(user)
    db.commit()
    for digest in ["session-a", "session-b"]:
        db.add(
            DbSession(user_id=user.id, token_digest=digest, expires_at=utcnow() + timedelta(days=1))
        )
    db.commit()
    request = Request(scope={"type": "http", "client": ("10.26.0.5", 1), "headers": []})
    result = auth.request_password_reset(auth.ResendCodeIn(email=user.email), request, db)
    assert result["ok"] and len(sent) == 1
    code = re.search(r"\d{6}", sent[0]).group()
    stored = db.scalar(
        select(auth.SystemConfig).where(auth.SystemConfig.key == f"password_reset:{user.email}")
    )
    assert "code" not in stored.value and len(stored.value["digest"]) == 64
    payload = auth.PasswordResetIn(email=user.email, code=code, password="NewAudit123")
    assert auth.confirm_password_reset(payload, db)["ok"]
    db.refresh(user)
    assert verify_password("NewAudit123", user.password_hash)
    assert not verify_password("OldAudit123", user.password_hash)
    assert not db.scalars(select(DbSession)).all()
    with pytest.raises(APIError):
        auth.confirm_password_reset(payload, db)


def test_reset_rejects_expired_wrong_and_register_code(db, monkeypatch):
    from app.api.errors import APIError
    from app.api.routers import auth

    user = User(email="reset-invalid@example.com", password_hash="unchanged", email_verified=True)
    db.add(user)
    db.add(auth.SystemConfig(key=f"email_code:{user.email}", value={"code": "123456"}))
    db.add(
        auth.SystemConfig(
            key=f"password_reset:{user.email}",
            value={
                "digest": auth._reset_code_digest(user.email, "654321"),
                "expires_at": (utcnow() - timedelta(minutes=1)).isoformat(),
            },
        )
    )
    db.commit()
    for code in ["123456", "654321"]:
        with pytest.raises(APIError) as error:
            auth.confirm_password_reset(
                auth.PasswordResetIn(email=user.email, code=code, password="NewAudit123"), db
            )
        assert error.value.code == "bad_reset_code"
    assert user.password_hash == "unchanged"


def test_reset_request_has_same_response_for_unknown_and_unverified(db, monkeypatch):
    from app.api.routers import auth

    monkeypatch.setattr(auth, "_pad_resend_timing", lambda start: None)
    sent = []
    monkeypatch.setattr(auth, "send_email", lambda *args: sent.append(args))
    db.add(User(email="unverified-reset@example.com", password_hash="x", email_verified=False))
    db.commit()
    request = Request(scope={"type": "http", "client": ("10.26.0.6", 1), "headers": []})
    one = auth.request_password_reset(
        auth.ResendCodeIn(email="unknown-reset@example.com"), request, db
    )
    two = auth.request_password_reset(
        auth.ResendCodeIn(email="unverified-reset@example.com"), request, db
    )
    assert one == two and not sent


def test_group_pause_is_scoped_and_keeps_task_quota(db):
    from app.schemas import TaskGroupActionIn

    users = [User(email=f"group{i}@example.com", password_hash="x") for i in range(2)]
    db.add_all(users)
    db.commit()
    for user, number in [(users[0], "R484"), (users[0], "R359"), (users[1], "R484")]:
        db.add(
            MonitorTask(
                user_id=user.id,
                name="同名购买目标",
                group="家人的手机",
                part_number="MJYC4CH/A",
                stores=[{"number": number}],
                store_numbers=[number],
                mode="instant",
                paused=False,
            )
        )
    db.commit()
    result = tasks_router.group_action(
        TaskGroupActionIn(group="家人的手机", paused=True), users[0], db
    )
    assert len(result) == 2 and all(t.paused and t.paused_reason == "manual" for t in result)
    other = db.scalar(select(MonitorTask).where(MonitorTask.user_id == users[1].id))
    assert not other.paused
    assert db.query(MonitorTask).count() == 3
    restored = tasks_router.group_action(
        TaskGroupActionIn(group="家人的手机", paused=False), users[0], db
    )
    assert all(not t.paused and t.paused_reason is None for t in restored)


@pytest.fixture
def group_fixture(db):
    owner = User(email="group-settings@example.com", password_hash="x", email_verified=True)
    other = User(email="group-other@example.com", password_hash="x", email_verified=True)
    db.add_all([owner, other])
    db.flush()
    tasks = []
    for user_id, part, interval in [(owner.id, "A", 300), (owner.id, "B", None),
                                    (other.id, "C", 600)]:
        task = MonitorTask(user_id=user_id, name=f"任务-{part}", group="同名目标",
                           part_number=part, stores=[{"number": "R359"}],
                           store_numbers=["R359"], mode="instant",
                           channels={"email": "old@example.com", "webhooks": [{"url": "x"}]},
                           repeat_interval_sec=interval, paused=True, paused_reason="manual")
        tasks.append(task)
        db.add(task)
    db.commit()
    return owner, tasks


def _group_payload(owner_tasks, **patch):
    from app.schemas import TaskGroupSettingsIn

    return TaskGroupSettingsIn(group="同名目标", expected=[
        {"id": task.id, "config_revision": tasks_router._config_revision(task)}
        for task in owner_tasks
    ], patch=patch)


def test_group_settings_are_atomic_owned_and_preserve_omitted_fields(db, group_fixture):
    owner, tasks = group_fixture
    payload = _group_payload(tasks[:2], group="新的购买目标", mode="confirmed",
                             repeat_interval_sec=None, email=None)
    result = tasks_router.patch_group_settings(payload, owner, db)
    assert len(result) == 2
    for task in tasks[:2]:
        assert task.group == "新的购买目标" and task.mode == "confirmed"
        assert task.repeat_interval_sec is None
        assert task.channels["email"] == owner.email
        assert task.channels["webhooks"] == [{"url": "x"}]
        assert task.paused and task.paused_reason == "manual"
        assert task.name.startswith("任务-") and task.store_numbers == ["R359"]
    assert tasks[2].group == "同名目标" and tasks[2].repeat_interval_sec == 600
    assert all(len(item.config_revision) == 64 for item in result)


@pytest.mark.parametrize("change", ["member", "settings", "foreign", "duplicate"])
def test_group_snapshot_conflict_does_not_partially_write(db, group_fixture, change):
    from app.api.errors import APIError
    from app.schemas import TaskConfigSnapshotIn

    owner, tasks = group_fixture
    payload = _group_payload(tasks[:2], mode="confirmed")
    if change == "member":
        tasks[1].group = "已移动目标"
        db.commit()
    elif change == "settings":
        tasks[1].repeat_interval_sec = 120
        db.commit()
    elif change == "foreign":
        payload.expected.append(TaskConfigSnapshotIn(
            id=tasks[2].id, config_revision=tasks_router._config_revision(tasks[2])))
    else:
        payload.expected.append(payload.expected[0])
    with pytest.raises(APIError) as exc:
        tasks_router.patch_group_settings(payload, owner, db)
    assert exc.value.status_code == 409
    db.rollback()
    assert all(task.mode == "instant" for task in tasks)


def test_group_partial_patch_preserves_different_reminders_and_rejects_name_collision(
    db, group_fixture,
):
    from app.api.errors import APIError

    owner, tasks = group_fixture
    payload = _group_payload(tasks[:2], email="new@example.com")
    tasks_router.patch_group_settings(payload, owner, db)
    assert tasks[0].repeat_interval_sec == 300 and tasks[1].repeat_interval_sec is None
    tasks[1].group = "另一个目标"
    db.commit()
    with pytest.raises(APIError) as exc:
        tasks_router.patch_group_settings(_group_payload(tasks[:1], group="另一个目标"), owner, db)
    assert exc.value.code == "group_exists"
    db.rollback()
    assert tasks[0].group == "同名目标"


def test_configuration_revision_ignores_poll_time_and_rejects_stale_edit(db, group_fixture):
    from app.api.errors import APIError
    from app.schemas import TaskPatchIn

    owner, tasks = group_fixture
    task = tasks[0]
    revision = tasks_router._config_revision(task)
    task.last_polled_at = utcnow()
    task.last_poll_ok = False
    db.commit()
    assert tasks_router._config_revision(task) == revision
    saved = tasks_router.patch_task(task.id, TaskPatchIn(
        config_revision=revision, mode="confirmed"), owner, db, None)
    assert saved.mode == "confirmed" and saved.config_revision != revision
    with pytest.raises(APIError) as exc:
        tasks_router.patch_task(task.id, TaskPatchIn(
            config_revision=revision, name="旧窗口覆盖"), owner, db, None)
    assert exc.value.code == "config_conflict"
    db.rollback()
    assert task.name == "任务-A"


@pytest.mark.parametrize("patch", [{}, {"mode": None}, {"group": " "}])
def test_group_invalid_patch_cannot_change_settings(db, group_fixture, patch):
    from app.api.errors import APIError

    owner, tasks = group_fixture
    with pytest.raises(APIError) as exc:
        tasks_router.patch_group_settings(_group_payload(tasks[:2], **patch), owner, db)
    assert exc.value.status_code == 400
    db.rollback()
    assert tasks[0].repeat_interval_sec == 300


def test_group_http_routes_and_conflict_payload(db, group_fixture):
    import app.main as main
    from app.api.deps import get_current_user
    from app.core.db import get_db
    from starlette.testclient import TestClient

    owner, tasks = group_fixture
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[get_current_user] = lambda: owner
    main.app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(main.app)
        result = client.get("/api/tasks/group-settings", params={"group": "同名目标"})
        assert result.status_code == 200, result.text
        snapshots = [{"id": t["id"], "config_revision": t["config_revision"]}
                     for t in result.json()]
        assert {t["id"] for t in snapshots} == {task.id for task in tasks[:2]}
        payload = {"group": "同名目标", "expected": snapshots,
                   "patch": {"repeat_interval_sec": None}}
        assert client.patch("/api/tasks/group-settings", json=payload).status_code == 200
        rejected = client.patch("/api/tasks/group-settings", json=payload)
        assert rejected.status_code == 409 and rejected.json()["code"] == "config_conflict"
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


def test_initial_available_sends_once_and_consumes_one_credit(db, monkeypatch):
    from app.models.models import QuotaUsage

    user = User(email="initial-arrival@example.com", password_hash="x", tier="free",
                email_verified=True)
    db.add(user)
    db.flush()
    task = MonitorTask(name="首轮到货", user_id=user.id, part_number="MJYC4CH/A",
                       store_numbers=["R484"], stores=[], mode="instant",
                       repeat_interval_sec=None, channels={"email": user.email})
    db.add(task)
    db.commit()
    sends = []
    poller = engine_module.Engine()

    def send(*args):
        sends.append(task.id)
        return 1, False

    monkeypatch.setattr(poller, "_fire", send)
    result = SimpleNamespace(state="available", pickup_display="available",
                             store_pick_eligible=True, pickup_search_quote=None)
    for _ in range(2):
        poller._process_task(db, task, (task.part_number,), ["R484"],
                             {("R484", task.part_number): result})
        db.commit()
    assert sends == [task.id]
    assert db.scalars(select(QuotaUsage)).one().push_count == 1
    assert db.scalars(select(StockState)).one().last_event_at is not None
