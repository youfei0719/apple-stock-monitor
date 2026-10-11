"""Advance setup activates on preorder and alerts only on real eligible stock."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.errors import APIError
from app.api.routers import tasks as router
from app.core import product_launch
from app.core.db import Base
from app.core.product_catalog import OFFICIAL_PRODUCTS
from app.models.models import MonitorTask, StockState, User
from app.schemas import TaskBatchIn, TaskCreateIn
from app.services import engine as engine_module
from app.services import lifecycle
from app.services.apple_client import AppleError, ProviderResult, StockResult

PART = next(p["part_number"] for p in OFFICIAL_PRODUCTS if p.get("launch"))
OPEN = datetime(2026, 10, 16, 12)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def clock(monkeypatch):
    current = [OPEN - timedelta(seconds=1)]
    for module in (product_launch, engine_module, router):
        monkeypatch.setattr(module, "utcnow", lambda: current[0])
    monkeypatch.setattr(lifecycle, "_utcnow", lambda: current[0])
    return current


def make_task(db, **kwargs):
    task = MonitorTask(
        name="新品预约",
        device_id="prelaunch-test",
        part_number=PART,
        store_numbers=["R359"],
        stores=[{"number": "R359", "name": "Apple 南京东路", "city": "上海"}],
        mode="instant",
        repeat_interval_sec=None,
        **kwargs,
    )
    db.add(task)
    db.commit()
    return task


def test_timezone_and_exact_preorder_boundary():
    assert product_launch.waiting_for_launch(PART, OPEN - timedelta(microseconds=1))
    assert not product_launch.waiting_for_launch(PART, OPEN)
    assert not product_launch.waiting_for_launch(
        PART, datetime(2026, 10, 16, 20, tzinfo=timezone(timedelta(hours=8)))
    )
    assert not product_launch.waiting_for_launch("UNKNOWN", OPEN)


def test_before_preorder_no_requests_or_notifications(db, clock):
    task = make_task(db)
    poller = engine_module.Engine()
    poller.client = SimpleNamespace(query=Mock())
    poller._fire = Mock()
    assert poller._due_tasks(db) == []
    assert poller._poll_group(db, "", (PART,), [task]) is False
    # Even a direct call with an accidental positive result must stay silent.
    row = StockResult("R359", "", PART, "available", "available", True)
    poller._process_task(db, task, (PART,), ["R359"], {("R359", PART): row})
    poller.client.query.assert_not_called()
    poller._fire.assert_not_called()
    assert db.query(StockState).count() == 0
    assert task.last_polled_at is None
    clock[0] = OPEN
    assert [t.id for t in poller._due_tasks(db)] == [task.id]


@pytest.mark.parametrize("mode,expected", [("instant", 1), ("confirmed", 2)])
def test_actual_stock_not_calendar_triggers_one_alert(db, clock, mode, expected):
    task = make_task(db)
    task.mode = mode
    db.commit()
    poller = engine_module.Engine()
    alerts = []
    poller._fire = lambda *args: (alerts.append(args[2]) or 1, False)
    poller._check_quota = lambda *args: True
    poller._consume_quota = lambda *args: None
    poller._maybe_quota_warning = lambda *args: None
    clock[0] = OPEN
    # Opening itself, a failed query, ineligibility, and actual no-stock stay silent.
    poller.client = SimpleNamespace(query=Mock(side_effect=AppleError("not opened")))
    poller._poll_group(db, "", (PART,), [task])
    for state, display, eligible in [
        ("unknown", "ineligible", False),
        ("unavailable", "unavailable", True),
    ]:
        row = StockResult("R359", "", PART, state, display, eligible)
        poller.client = SimpleNamespace(query=Mock(return_value=ProviderResult(results=[row])))
        poller._poll_group(db, "", (PART,), [task])
    assert alerts == []
    row = StockResult("R359", "", PART, "available", "available", True)
    poller.client = SimpleNamespace(query=Mock(return_value=ProviderResult(results=[row])))
    for n in range(3):
        poller._poll_group(db, "", (PART,), [task])
        assert len(alerts) == (1 if n + 1 >= expected else 0)
    assert router._task_out(task, db).latest["available_count"] == 1


def test_waiting_display_cannot_leak_stale_available_and_pause_wins(db, clock):
    task = make_task(db)
    row = StockState(
        task_id=task.id,
        store_number="R359",
        part_number=PART,
        state="available",
        pickup_display="available",
        store_pick_eligible=True,
    )
    db.add(row)
    db.commit()
    assert router._display_state(task, row) == "scheduled"
    assert router._task_out(task, db).latest["available_count"] == 0
    task.expires_at = clock[0] - timedelta(seconds=1)
    assert router._display_state(task, row) == "expired"
    task.paused = True
    assert router._display_state(task, row) == "paused"


def request():
    return Request(scope={"type": "http", "client": ("10.29.0.1", 1234), "headers": []})


def test_anonymous_cannot_save_short_lived_prelaunch_task(db, clock):
    payload = TaskCreateIn(
        name="预约",
        part_number=PART,
        stores=[{"number": "R359", "name": "Apple 南京东路", "city": "上海"}],
    )
    with pytest.raises(APIError) as error:
        router.create_task(payload, request(), None, db, "prelaunch-anon")
    assert error.value.status_code == 403
    assert error.value.code == "prelaunch_login_required"
    assert db.query(MonitorTask).count() == 0


def test_registered_batch_persists_config_without_expiring_before_launch(db, clock):
    user = User(
        email="prelaunch@example.com", password_hash="test", tier="free", email_verified=True
    )
    db.add(user)
    db.commit()
    payload = TaskBatchIn(
        part_numbers=[PART],
        store_numbers=["R359", "R484"],
        mode="instant",
        repeat_interval_sec=None,
    )
    tasks = router.batch_create(payload, request(), user, db)
    assert len(tasks) == 2
    assert all(t.waiting_for_launch and t.launch["preorder_at"] for t in tasks)
    assert all(t.expires_at is None and t.channels["email"] == user.email for t in tasks)
    assert all("iPhone Duo" in t.product_name and t.color and t.capacity for t in tasks)
    assert len({t.stores[0]["number"] for t in tasks}) == 2


def test_prelaunch_wait_does_not_count_as_long_term_shortage(db, clock):
    task = make_task(db, created_at=OPEN - timedelta(days=120))
    db.add(
        StockState(
            task_id=task.id,
            store_number="R359",
            part_number=PART,
            state="unavailable",
            updated_at=clock[0],
        )
    )
    db.commit()
    assert lifecycle.zombie_sweep(db) == {"nudges": 0, "auto_paused": 0}
    clock[0] = OPEN + timedelta(days=1)
    assert lifecycle.zombie_sweep(db) == {"nudges": 0, "auto_paused": 0}
    assert task.paused is False
