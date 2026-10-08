"""round17 后端修复回归测试。

R17-P1-1：_flush_api_hits 刷盘逻辑曾把 batch 与 buffer 指向同一对象，
随后 clear 连 batch 一起清空 → if not batch 恒成立 → 永远写 0 行
（且这是 api_hits 表唯一写入路径，后台"流量"页恒为空）。
回归：buffer 塞 N 条 → _flush_api_hits() → ApiHit 表行数 == N。
"""

import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.main as main
from app.core.db import Base
from app.models.models import ApiHit


@pytest.fixture
def mem_session():
    """内存库 + 劫持 _flush_api_hits 用的 SessionLocal。"""
    e = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e)
    sm = sessionmaker(bind=e)
    old = main.SessionLocal
    main.SessionLocal = sm
    yield sm
    main.SessionLocal = old
    main._api_hit_buffer.clear()


def test_flush_api_hits_writes_all_buffered_rows(mem_session):
    n = 5
    main._api_hit_buffer.clear()
    for i in range(n):
        main._api_hit_buffer.append((f"/api/test{i}", "h" * 32))

    main._flush_api_hits()

    db = mem_session()
    try:
        assert db.query(ApiHit).count() == n
    finally:
        db.close()
    # 刷盘后 buffer 已换空，不能残留
    assert main._api_hit_buffer == []


def test_flush_api_hits_empty_buffer_is_noop(mem_session):
    main._api_hit_buffer.clear()
    main._flush_api_hits()
    db = mem_session()
    try:
        assert db.query(ApiHit).count() == 0
    finally:
        db.close()
