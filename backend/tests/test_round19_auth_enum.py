"""round19 后端回归测试。

R19-P2-1（安全·用户枚举）：/verify-email 与 /resend-code 曾对"已验证邮箱"
返回 200 {"ok":true,"already":true}，而未注册邮箱返回 400/无 flag，可逐个
探测邮箱是否为平台已验证用户；且该 200 路径不消耗 attempt-lock，可全速探测。
修复：删掉两处"已验证"早退——verify-email 统一走验证码比对（已验证用户的
码行已删除，比对恒失败 → 400，与未知邮箱不可区分）；resend-code 统一返回
无 flag 的 {"ok": true}。

回归：已验证 / 未注册 / 未验证三种邮箱，verify-email 与 resend-code 的响应
在状态码 + body 形状上完全不可区分。

R19-P3-1（存储滥用）：verify_email 未知邮箱分支曾 _record_attempt_fail →
system_config 落 junk 行且无清理。修复：未知邮箱不落库。
回归：未知邮箱调 verify-email 后，system_config 无 verify_email_fail 行。
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
import app.main as main
from app.api.routers.auth import EMAIL_CODE_KEY_PREFIX
from app.core.db import Base, get_db
from app.core.ratelimit import _hit_windows
from app.models.models import SystemConfig, User

UNVERIFIED_EMAIL = "r19-unverified@example.com"
VERIFIED_EMAIL = "r19-verified@example.com"
UNKNOWN_EMAIL = "r19-unknown@example.com"
PASSWORD = "R19testpw"


@pytest.fixture
def client(monkeypatch):
    """内存库 + 依赖注入劫持 get_db；send_email 打桩为 no-op（测试环境无 SMTP）。"""
    monkeypatch.setattr(auth_router, "send_email", lambda *a, **k: None)
    # 限流器是进程级全局状态（register 5 次/小时/IP），清掉避免被同进程
    # 其他测试文件的注册调用耗尽配额——纯测试隔离，不影响产品行为。
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

    main.app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(main.app) as c:
            yield c, testing_session
    finally:
        main.app.dependency_overrides.pop(get_db, None)


def _read_email_code(testing_session, email: str) -> str:
    db = testing_session()
    try:
        row = db.execute(
            select(SystemConfig).where(SystemConfig.key == f"{EMAIL_CODE_KEY_PREFIX}{email}")
        ).scalar_one()
        return row.value["code"]
    finally:
        db.close()


def _setup_three_emails(c: TestClient, testing_session) -> None:
    # 未验证：只注册
    r = c.post(
        "/api/auth/register",
        json={"email": UNVERIFIED_EMAIL, "password": PASSWORD},
    )
    assert r.status_code == 201, r.text
    # 已验证：注册 + 用正确验证码验证
    r = c.post("/api/auth/register", json={"email": VERIFIED_EMAIL, "password": PASSWORD})
    assert r.status_code == 201, r.text
    code = _read_email_code(testing_session, VERIFIED_EMAIL)
    r = c.post("/api/auth/verify-email", json={"email": VERIFIED_EMAIL, "code": code})
    assert r.status_code == 200, r.text
    db = testing_session()
    try:
        user = db.execute(select(User).where(User.email == VERIFIED_EMAIL)).scalar_one()
        assert user.email_verified is True
    finally:
        db.close()
    # UNKNOWN_EMAIL：不注册


def _indistinguishable(responses) -> None:
    """三种邮箱的响应：状态码相同，且 body JSON 逐字节一致。"""
    statuses = {r.status_code for r in responses}
    assert len(statuses) == 1, f"状态码可区分: {[(r.status_code) for r in responses]}"
    bodies = [r.json() for r in responses]
    assert all(b == bodies[0] for b in bodies), f"body 可区分: {bodies}"


def test_verify_email_responses_indistinguishable(client):
    """verify-email：已验证/未注册/未验证邮箱，用错码调用 → 响应不可区分。"""
    c, testing_session = client
    _setup_three_emails(c, testing_session)
    responses = [
        c.post("/api/auth/verify-email", json={"email": e, "code": "000000"})
        for e in (VERIFIED_EMAIL, UNKNOWN_EMAIL, UNVERIFIED_EMAIL)
    ]
    assert all(r.status_code == 400 for r in responses)
    _indistinguishable(responses)
    body = responses[0].json()
    assert body == {"detail": "验证码错误", "code": "bad_code"}
    assert "already" not in body


def test_resend_code_responses_indistinguishable(client):
    """resend-code：已验证/未注册/未验证邮箱 → 一律 200 {"ok": true}，无 flag。"""
    c, testing_session = client
    _setup_three_emails(c, testing_session)
    responses = [
        c.post("/api/auth/resend-code", json={"email": e})
        for e in (VERIFIED_EMAIL, UNKNOWN_EMAIL, UNVERIFIED_EMAIL)
    ]
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    _indistinguishable(responses)
    assert responses[0].json() == {"ok": True}


def test_verify_email_unknown_email_writes_no_attempt_row(client):
    """R19-P3-1：未知邮箱调 verify-email 不在 system_config 落 attempt 行。"""
    c, testing_session = client
    r = c.post("/api/auth/verify-email", json={"email": UNKNOWN_EMAIL, "code": "000000"})
    assert r.status_code == 400
    db = testing_session()
    try:
        row = db.execute(
            select(SystemConfig).where(SystemConfig.key == f"verify_email_fail:{UNKNOWN_EMAIL}")
        ).scalar_one_or_none()
        assert row is None, "未知邮箱不应产生 verify_email_fail 落库行"
    finally:
        db.close()
