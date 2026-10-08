"""round16 后端修复回归测试。

R16-P2-1：httpx 传输层异常（ConnectError/TimeoutException 等）必须收敛到
AppleError 体系——此前原样上抛，引擎 _poll_group 接不住（本轮剩余分组全跳过
且失败分组不标 unknown），门店目录刷新 _do_refresh_stores 接不住
（REFRESH_AT_KEY 不清零，管理员被锁 1 小时冷却）。

注意：传输失败归入 AppleError 而非 AppleRateLimitError（不触发指数退避冷却）。
"""

import os
import sys
import time

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.routers import catalog as catalog_router
from app.core.db import Base
from app.models.models import MonitorTask
from app.services.apple_client import (
    AppleClient,
    AppleError,
    AppleRateLimitError,
    BaseProvider,
    FulfillmentMessagesProvider,
    PickupMessageProvider,
)
from app.services.engine import Engine, get_config, set_config


class _RaisingClient:
    """桩 httpx.Client：get 直接抛指定传输异常（模拟断网/超时）。"""

    def __init__(self, exc: Exception):
        self._exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, *args, **kwargs):
        raise self._exc


def _break_network(monkeypatch, exc):
    """让所有 provider 的 _client 返回抛传输异常的桩。"""
    monkeypatch.setattr(
        BaseProvider, "_client", lambda self: _RaisingClient(exc)
    )


@pytest.fixture
def db():
    e = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e)
    s = sessionmaker(bind=e)()
    yield s
    s.close()


def test_pickup_provider_transport_connect_error_becomes_apple_error(monkeypatch):
    _break_network(monkeypatch, httpx.ConnectError("connection refused"))
    p = PickupMessageProvider(timeout=5)
    with pytest.raises(AppleError) as ei:
        p.query(["MJYC4CH/A"], ["R484"])
    assert not isinstance(ei.value, AppleRateLimitError)  # 不归入限流退避
    assert "transport error" in str(ei.value)
    assert "pickup-message" in str(ei.value)


def test_fulfillment_provider_transport_timeout_becomes_apple_error(monkeypatch):
    _break_network(monkeypatch, httpx.TimeoutException("timed out"))
    p = FulfillmentMessagesProvider(timeout=5)
    with pytest.raises(AppleError) as ei:
        p.query(["MJYC4CH/A"], ["R484"])
    assert not isinstance(ei.value, AppleRateLimitError)
    assert "transport error" in str(ei.value)


def test_discover_stores_transport_error_becomes_apple_error(monkeypatch):
    _break_network(monkeypatch, httpx.ConnectError("dns failed"))
    c = AppleClient()
    with pytest.raises(AppleError) as ei:
        c.discover_stores("深圳")
    assert not isinstance(ei.value, AppleRateLimitError)


def test_apple_client_query_transport_error_not_raw_httpx(monkeypatch):
    """全 provider 传输失败：上抛 AppleError（引擎 except AppleError 接得住），
    而不是原生的 httpx.RequestError。"""
    _break_network(monkeypatch, httpx.ConnectError("connection refused"))
    c = AppleClient()
    with pytest.raises(AppleError) as ei:
        c.query(["MJYC4CH/A"], ["R484"])
    assert not isinstance(ei.value, httpx.RequestError)


def test_engine_poll_group_marks_unknown_on_transport_error(db, monkeypatch):
    """R16-P2-1 核心回归：传输异常时 _poll_group 标 unknown、返回 False
    （继续本轮其他分组），而不是把 httpx 异常抛给 tick 导致整轮跳过。"""
    _break_network(monkeypatch, httpx.ConnectError("connection refused"))
    task = MonitorTask(
        name="t1",
        part_number="MJYC4CH/A",
        store_numbers=["R484"],
        stores=[{"number": "R484", "name": "益田假日广场", "city": "深圳"}],
    )
    db.add(task)
    db.commit()

    eng = Engine()
    stopped = eng._poll_group(db, "深圳", ("MJYC4CH/A",), [task])
    assert stopped is False  # 非限流：调用方继续其他分组
    row = eng._get_state(db, task.id, "R484", "MJYC4CH/A")
    assert row.state == "unknown"  # 请求失败一律 unknown，绝不当无货
    assert task.last_poll_ok is False


def test_catalog_refresh_clears_cooldown_on_transport_error(monkeypatch):
    """R16-P2-1 回归：门店目录刷新遇到传输异常（已转为 AppleError），
    全失败分支清零 REFRESH_AT_KEY 占位时间，管理员不被锁 1 小时。"""
    e = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e)
    maker = sessionmaker(bind=e)
    monkeypatch.setattr(catalog_router, "SessionLocal", maker)
    monkeypatch.setattr(catalog_router, "_refresh_running", False)
    _break_network(monkeypatch, httpx.ConnectError("connection refused"))

    db = maker()
    set_config(db, catalog_router.REFRESH_AT_KEY, {"at": time.time()})  # 模拟入队占位
    db.close()

    catalog_router._do_refresh_stores()

    db = maker()
    at = get_config(db, catalog_router.REFRESH_AT_KEY, {}).get("at")
    db.close()
    assert at == 0
