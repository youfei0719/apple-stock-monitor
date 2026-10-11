"""真机审计发现的 Apple 解析、分批调度和任务转入回归。"""

import os
import sys
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import Request
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.api.routers import auth
from app.api.routers import tasks as tasks_router
from app.core.db import Base
from app.core.timeutil import utcnow
from app.models.models import MonitorTask, StockState, User
from app.services.apple_client import (
    AppleClient,
    AppleError,
    AppleRateLimitError,
    BaseProvider,
    ProviderResult,
    StockResult,
    unknown_reason,
)
from app.services.engine import Engine


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def response(pd="ineligible", eligible=True):
    return {
        "body": {
            "stores": [
                {
                    "storeNumber": "R471",
                    "partsAvailability": {
                        "P1": {"pickupDisplay": pd, "storePickEligible": eligible}
                    },
                }
            ]
        }
    }


@pytest.mark.parametrize(
    "data",
    [
        [],
        {},
        {"body": []},
        {"body": {"stores": {}}},
        {"body": {"stores": []}},
        {"body": {"stores": [{"storeNumber": "R471", "partsAvailability": []}]}},
    ],
)
def test_invalid_success_response_is_not_a_successful_query(data):
    with pytest.raises(AppleError):
        BaseProvider(5)._parse_stores(data, ["P1"])


def test_ineligible_is_explained_without_claiming_out_of_stock():
    row = BaseProvider(5)._parse_stores(response(), ["P1"])[0]
    assert row.state == "unknown"
    assert (
        unknown_reason(row.state, row.pickup_display, row.store_pick_eligible)
        == "pickup_not_offered"
    )


@pytest.mark.parametrize("pd", ["", {}, [], 1])
def test_malformed_display_rejected_before_database_write(pd):
    with pytest.raises(AppleError):
        BaseProvider(5)._parse_stores(response(pd), ["P1"])


@pytest.mark.parametrize("eligible", ["false", "true", 1, None])
def test_malformed_eligibility_cannot_send_false_stock_notification(eligible):
    row = BaseProvider(5)._parse_stores(response("available", eligible), ["P1"])[0]
    assert row.state == "unknown"
    assert row.store_pick_eligible is None


def test_provider_empty_result_falls_back_and_discards_other_stores():
    client = AppleClient()
    client.providers = [
        SimpleNamespace(name="empty", query=lambda *args: ProviderResult()),
        SimpleNamespace(
            name="valid",
            query=lambda *args: ProviderResult(
                results=[
                    StockResult("R471", "", "P1", "unavailable", "unavailable", True),
                    StockResult("OTHER", "", "P1", "available", "available", True),
                ]
            ),
        ),
    ]
    assert [r.store_number for r in client.query(["P1"], ["R471"]).results] == ["R471"]


def test_rate_limit_is_not_lost_when_backup_fails():
    def limited(*args):
        raise AppleRateLimitError("541")

    def failed(*args):
        raise AppleError("invalid backup")

    client = AppleClient()
    client.providers = [
        SimpleNamespace(name="primary", query=limited),
        SimpleNamespace(name="backup", query=failed),
    ]
    with pytest.raises(AppleRateLimitError):
        client.query(["P1"], ["R471"])


def test_backup_fills_missing_stores_without_replacing_primary_results():
    client = AppleClient()
    client.providers = [
        SimpleNamespace(
            name="primary",
            query=lambda *args: ProviderResult(
                results=[StockResult("S1", "", "P1", "unavailable", "unavailable", True)]
            ),
        ),
        SimpleNamespace(
            name="backup",
            query=lambda *args: ProviderResult(
                results=[
                    StockResult("S1", "", "P1", "available", "available", True),
                    StockResult("S2", "", "P1", "unknown", "ineligible", True),
                ]
            ),
        ),
    ]
    rows = {(r.store_number, r.part_number): r for r in client.query(["P1"], ["S1", "S2"]).results}
    assert rows[("S1", "P1")].state == "unavailable"
    assert rows[("S2", "P1")].pickup_display == "ineligible"


def test_partial_results_preserved_when_no_provider_can_fill_missing_store():
    client = AppleClient()
    client.providers = [
        SimpleNamespace(
            name="partial",
            query=lambda *args: ProviderResult(
                results=[StockResult("S1", "", "P1", "unavailable", "unavailable", True)]
            ),
        )
    ]
    assert [r.store_number for r in client.query(["P1"], ["S1", "S2"]).results] == ["S1"]


def make_task(db, stores, device="test"):
    task = MonitorTask(
        name="目标",
        device_id=device,
        part_number="P1",
        store_numbers=stores,
        stores=[{"number": n} for n in stores],
        mode="instant",
    )
    db.add(task)
    db.commit()
    return task


def test_batch_failure_cannot_be_overwritten_by_later_success(db):
    task = make_task(db, ["S1", "S2"])
    engine = Engine()
    engine.settings = engine.settings.model_copy(update={"APPLE_MAX_STORES_PER_REQ": 1})

    def query(parts, stores):
        if stores == ["S1"]:
            raise AppleError("timeout")
        return ProviderResult(
            results=[StockResult("S2", "", "P1", "unavailable", "unavailable", True)]
        )

    engine.client = SimpleNamespace(query=query)
    engine._poll_group(db, "", ("P1",), [task])
    assert task.last_poll_ok is False
    rows = db.scalars(select(StockState)).all()
    assert {r.store_number: r.state for r in rows} == {"S1": "unknown", "S2": "unavailable"}


def test_group_members_only_store_their_own_stores(db):
    a = make_task(db, ["S1"], "a")
    b = make_task(db, ["S2"], "b")
    engine = Engine()
    engine.client = SimpleNamespace(
        query=lambda *args: ProviderResult(
            results=[
                StockResult(n, "", "P1", "unavailable", "unavailable", True) for n in ["S1", "S2"]
            ]
        )
    )
    engine._poll_group(db, "", ("P1",), [a, b])
    assert {(r.task_id, r.store_number) for r in db.scalars(select(StockState))} == {
        (a.id, "S1"),
        (b.id, "S2"),
    }


def test_missing_result_clears_old_raw_stock_and_does_not_look_complete(db):
    task = make_task(db, ["S1", "S2"])
    db.add(
        StockState(
            task_id=task.id,
            store_number="S2",
            part_number="P1",
            state="available",
            prev_known="available",
            pickup_display="available",
            store_pick_eligible=True,
        )
    )
    db.commit()
    engine = Engine()
    engine.client = SimpleNamespace(
        query=lambda *args: ProviderResult(
            results=[StockResult("S1", "", "P1", "unavailable", "unavailable", True)]
        )
    )
    engine._poll_group(db, "", ("P1",), [task])
    row = db.scalars(select(StockState).where(StockState.store_number == "S2")).one()
    assert row.state == "unknown" and row.prev_known == "available"
    assert row.pickup_display is None and row.store_pick_eligible is None
    assert task.last_poll_ok is False


def test_unknown_reason_is_returned_in_detail_and_summary(db):
    task = make_task(db, ["S1"])
    db.add(
        StockState(
            task_id=task.id,
            store_number="S1",
            part_number="P1",
            state="unknown",
            pickup_display="ineligible",
            store_pick_eligible=True,
        )
    )
    db.commit()
    detail = tasks_router.task_states(task.id, None, db, "test")
    summary = tasks_router._task_out(task, db)
    assert detail[0]["unknown_reason"] == "pickup_not_offered"
    assert summary.latest["stores"]["S1"]["P1"]["unknown_reason"] == "pickup_not_offered"
    assert summary.product_url == "https://www.apple.com.cn/shop/product/p1"


def test_claim_extends_trial_without_restoring_user_pause(db):
    task = make_task(db, ["S1"], "claim")
    task.paused = True
    task.expires_at = utcnow() + timedelta(hours=24)
    user = User(
        email="claim-safari@example.com", password_hash="x", tier="free", email_verified=True
    )
    db.add(user)
    db.commit()
    before = utcnow()
    info = auth._claim_device_tasks(
        db, Request({"type": "http", "headers": [(b"x-device-id", b"claim")]}), user
    )
    db.commit()
    assert task.user_id == user.id and task.device_id is None
    assert task.paused is True
    assert task.expires_at >= before + timedelta(days=30)
    assert "30 天" in auth._claim_notice(info)
