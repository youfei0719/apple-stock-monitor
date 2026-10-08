"""round20 后端回归测试。

R20-P3-4（部署断裂·后台无法登录）：_bootstrap_admin 建管理员时未置
email_verified=True，而 login 对未验证账号一律 403 email_unverified，
且 admin 后台无邮箱验证入口 → 首次部署的管理员被挡在后台登录之外。
修复：bootstrap 直接置 user.email_verified=True（管理员邮箱是部署者
自己在 .env 配的可信地址）。
回归：bootstrap 出来的管理员行 email_verified 为 True，且能直接
POST /api/auth/login（200，不再 403 email_unverified）。

R20-P3-5（安全·计时侧信道）：resend-code 未注册邮箱瞬时返回，已注册
走 SMTP 实发（秒级），响应时长可区分出邮箱是否注册。
修复：未注册分支加与发信耗时同量级的随机延迟（UNKNOWN_EMAIL_DELAY_RANGE）。
回归：未注册邮箱调用 resend-code 时 time.sleep 被调用一次、时长落在
配置区间内；返回仍为无 flag 的 {"ok": true}。
"""

import os
import sys

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.api.routers.auth as auth_router
import app.main as main_mod
from app.core.db import Base, get_db
from app.core.ratelimit import _hit_windows
from app.models.models import User

ADMIN_EMAIL = "r20-boss@example.com"
ADMIN_PASSWORD = "r20-secret-123"


@pytest.fixture
def client(monkeypatch):
    """内存库 + 依赖注入劫持 get_db + SessionLocal 指向测试库。

    计时 padding 区间置 (0, 0)，避免回归单测被 1.5-3.5s sleep 拖慢；
    测 padding 行为的用例自行覆盖该值。
    """
    monkeypatch.setattr(auth_router, "UNKNOWN_EMAIL_DELAY_RANGE", (0, 0))
    _hit_windows.clear()
    e = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(e)
    testing_session = sessionmaker(bind=e, autoflush=False, autocommit=False)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(main_mod, "SessionLocal", testing_session)
    monkeypatch.setattr(main_mod.settings, "ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setattr(main_mod.settings, "ADMIN_PASSWORD", ADMIN_PASSWORD)
    main_mod.app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(main_mod.app) as c:
            yield c, testing_session
    finally:
        main_mod.app.dependency_overrides.pop(get_db, None)


def _bootstrap():
    main_mod._bootstrap_admin()


# ---------- R20-P3-4：bootstrap 管理员邮箱已验证 + 可直接登录 ----------


def test_r20_bootstrap_admin_email_verified(client):
    _, testing_session = client
    _bootstrap()
    db = testing_session()
    try:
        admin = db.execute(select(User).where(User.is_admin.is_(True))).scalar_one()
        assert admin.email_verified is True, "bootstrap 管理员必须直接已验证"
    finally:
        db.close()


def test_r20_bootstrap_admin_can_login_directly(client):
    """bootstrap 出来的管理员用配置密码直接登录成功（200），不再 403 email_unverified。"""
    c, _ = client
    _bootstrap()
    r = c.post(
        "/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert r.status_code == 200, f"管理员应能直接登录：{r.status_code} {r.text}"
    assert r.json().get("ok") is True


# ---------- R20-P3-5：resend-code 未注册分支计时 padding ----------


def test_r20_resend_unknown_email_padded(client, monkeypatch):
    """未注册邮箱：返回仍为无 flag 的 {"ok": true}，且 sleep 一次、时长落在区间内。"""
    c, _ = client
    # 本用例覆盖 fixture 的 (0, 0)，验证真实 padding 行为
    monkeypatch.setattr(auth_router, "UNKNOWN_EMAIL_DELAY_RANGE", (1.5, 3.5))
    calls = []
    monkeypatch.setattr(auth_router.time, "sleep", lambda s: calls.append(s))
    r = c.post("/api/auth/resend-code", json={"email": "r20-nobody@example.com"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}, "未知邮箱分支不得携带可枚举 flag"
    assert len(calls) == 1, f"未注册分支应 sleep 一次，实际 {calls}"
    assert 1.5 <= calls[0] <= 3.5, f"padding 时长应与发信耗时同量级：{calls}"
