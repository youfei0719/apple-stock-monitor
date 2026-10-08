"""round18 后端回归测试。

R18-P1-1：前端 changePassword 曾调错路径（PATCH /api/me → 405），正确路由是
PATCH /api/auth/me（auth.router prefix="/auth"；me_router 只挂了 GET /api/me 别名，
没有 PATCH）。此前后端单测只调了 patch_me 函数本体，没覆盖 HTTP 路径。
回归：走真实 HTTP 全链路——注册→验证→登录→PATCH /api/auth/me → 200；
并锁定 PATCH /api/me → 405（前端再调错会立刻被这个断言抓住）。
"""

import os
import sys

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.main as main
from app.api.routers.auth import EMAIL_CODE_KEY_PREFIX
from app.core.db import Base, get_db
from app.models.models import SystemConfig

EMAIL = "r18-changepw@example.com"
OLD_PASSWORD = "OldPass1234"
NEW_PASSWORD = "NewPass5678"


@pytest.fixture
def client():
    """内存库 + 依赖注入劫持 get_db；TestClient 跑真实 HTTP（含路由路径匹配）。"""
    # StaticPool：TestClient 在 portal 线程里处理请求，内存库必须跨线程共享同一连接
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

    main.app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(main.app) as c:
            yield c, testing_session
    finally:
        main.app.dependency_overrides.pop(get_db, None)


def _read_email_code(testing_session) -> str:
    db = testing_session()
    try:
        row = db.execute(
            select(SystemConfig).where(SystemConfig.key == f"{EMAIL_CODE_KEY_PREFIX}{EMAIL}")
        ).scalar_one()
        return row.value["code"]
    finally:
        db.close()


def _register_verify_login(c: TestClient, testing_session) -> None:
    r = c.post("/api/auth/register", json={"email": EMAIL, "password": OLD_PASSWORD})
    assert r.status_code == 201, r.text
    code = _read_email_code(testing_session)
    r = c.post("/api/auth/verify-email", json={"email": EMAIL, "code": code})
    assert r.status_code == 200, r.text
    r = c.post("/api/auth/login", json={"email": EMAIL, "password": OLD_PASSWORD})
    assert r.status_code == 200, r.text
    # 登录后 session_token cookie 已由 TestClient 持有，后续请求自动带上


def test_change_password_full_http_path(client):
    """R18-P1-1 主回归：注册→验证→登录→PATCH /api/auth/me → 200，新密码可登录。"""
    c, testing_session = client
    _register_verify_login(c, testing_session)

    r = c.patch(
        "/api/auth/me",
        json={"old_password": OLD_PASSWORD, "password": NEW_PASSWORD},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    # 新密码真的生效
    c.cookies.clear()
    r = c.post("/api/auth/login", json={"email": EMAIL, "password": NEW_PASSWORD})
    assert r.status_code == 200, r.text
    # 旧密码已失效
    c.cookies.clear()
    r = c.post("/api/auth/login", json={"email": EMAIL, "password": OLD_PASSWORD})
    assert r.status_code == 401, r.text


def test_change_password_wrong_old_password_400(client):
    """旧密码错误 → 400 bad_old_password（非 200），防误判成功。"""
    c, testing_session = client
    _register_verify_login(c, testing_session)

    r = c.patch(
        "/api/auth/me",
        json={"old_password": "WrongOld9999", "password": NEW_PASSWORD},
    )
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "bad_old_password"


def test_change_password_wrong_path_is_405(client):
    """锁定原始 bug：PATCH /api/me 不存在（me_router 只有 GET 别名），必须 405。

    前端 changePassword 曾调这个路径（全链路 405，用户点"修改密码"永远失败）。
    """
    c, testing_session = client
    _register_verify_login(c, testing_session)

    r = c.patch(
        "/api/me",
        json={"old_password": OLD_PASSWORD, "password": NEW_PASSWORD},
    )
    assert r.status_code == 405, r.text
