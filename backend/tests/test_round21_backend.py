"""round21 后端回归测试。

R21-P3-1（安全·计时侧信道）：login 未知邮箱分支直接 401（中位 0.004s），
已注册密码错误 401 中位 0.24s（Argon2），单样本可区分邮箱是否注册；
同函数 R10-P2-4 已给"未验证"分支加了 dummy verify_password，此处漏了。
修复：未知分支同样跑一次对模块级 _DUMMY_HASH 的 verify_password
（结果丢弃），与 R10-P2-4 同口径。
回归：未知邮箱 401 时 verify_password 被调用一次、第二个参数正是
_DUMMY_HASH；已注册密码错误 401 时同样调用一次、第二个参数是用户
真实 hash（两分支都走了同量级 Argon2 校验）。

R21-P3-2（安全·计时收敛）：resend-code 只有未注册分支垫了
uniform(1.5,3.5)s；已注册分支走真实 SMTP（典型 0.3–2s），SMTP 快于
1.5s 时仍可区分。
修复：两分支统一垫到公共下限——分支开始记 time.monotonic()，结束时
sleep(max(0, uniform(1.5,3.5) - elapsed))，500 email_failed 分支垫完再抛。
回归：已注册分支（成功与发信失败两条路径）同样 sleep 一次、补足量落在
配置区间内。
"""

import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.api.routers.auth as auth_router
import app.main as main_mod
from app.core.db import Base, get_db
from app.core.ratelimit import _hit_windows, _login_state
from app.core.security import hash_password
from app.models.models import User

PASSWORD = "r21-secret-123"


@pytest.fixture
def client(monkeypatch):
    """内存库 + 依赖注入劫持 get_db。

    计时 padding 区间置 (0, 0)，避免回归单测被 1.5-3.5s sleep 拖慢；
    测 padding 行为的用例自行覆盖该值。清掉进程级限流/登录失败状态，
    避免被同进程其他测试文件耗尽配额或触发 IP 锁定——纯测试隔离，
    不影响产品行为。
    """
    monkeypatch.setattr(auth_router, "UNKNOWN_EMAIL_DELAY_RANGE", (0, 0))
    _hit_windows.clear()
    _login_state.clear()
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
    main_mod.app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(main_mod.app) as c:
            yield c, testing_session
    finally:
        main_mod.app.dependency_overrides.pop(get_db, None)


def _make_verified_user(testing_session, email):
    db = testing_session()
    try:
        u = User(
            email=email,
            password_hash=hash_password(PASSWORD),
            email_verified=True,
        )
        db.add(u)
        db.commit()
        return u.password_hash
    finally:
        db.close()


def _record_verify(monkeypatch):
    """把 auth_router.verify_password 换成记录调用的桩（恒返回 False）。"""
    calls = []
    monkeypatch.setattr(
        auth_router,
        "verify_password",
        lambda pw, hashed: calls.append((pw, hashed)) or False,
    )
    return calls


# ---------- R21-P3-1：login 未知邮箱分支 dummy 校验 ----------


def test_r21_login_unknown_email_runs_dummy_verify(client, monkeypatch):
    """未知邮箱 401：verify_password 被调用一次，hash 参数正是 _DUMMY_HASH。"""
    c, _ = client
    calls = _record_verify(monkeypatch)
    r = c.post(
        "/api/auth/login",
        json={"email": "r21-nobody@example.com", "password": PASSWORD},
    )
    assert r.status_code == 401, f"未知邮箱应 401：{r.status_code} {r.text}"
    assert r.json().get("code") == "bad_credentials"
    assert len(calls) == 1, f"未知分支应跑一次 dummy 校验，实际 {calls}"
    pw, hashed = calls[0]
    assert pw == PASSWORD
    assert hashed == auth_router._DUMMY_HASH, "未知分支必须用模块级 _DUMMY_HASH 做 dummy 校验"


def test_r21_login_wrong_password_runs_real_verify(client, monkeypatch):
    """已注册密码错误 401：verify_password 被调用一次，hash 参数是用户真实 hash。"""
    c, testing_session = client
    email = "r21-user@example.com"
    real_hash = _make_verified_user(testing_session, email)
    calls = _record_verify(monkeypatch)
    r = c.post(
        "/api/auth/login",
        json={"email": email, "password": "wrong-password"},
    )
    assert r.status_code == 401, f"密码错误应 401：{r.status_code} {r.text}"
    assert r.json().get("code") == "bad_credentials"
    assert len(calls) == 1, f"密码错误分支应跑一次真实校验，实际 {calls}"
    pw, hashed = calls[0]
    assert pw == "wrong-password"
    assert hashed == real_hash, "已注册分支必须用用户真实 hash 校验"


# ---------- R21-P3-2：resend-code 两分支统一垫到公共下限 ----------


def _record_sleep(monkeypatch):
    calls = []
    monkeypatch.setattr(auth_router.time, "sleep", lambda s: calls.append(s))
    return calls


def test_r21_resend_registered_branch_padded(client, monkeypatch):
    """已注册分支（SMTP 极快时）同样垫到公共下限：sleep 一次、补足量落在区间内。"""
    c, testing_session = client
    monkeypatch.setattr(auth_router, "UNKNOWN_EMAIL_DELAY_RANGE", (1.5, 3.5))
    monkeypatch.setattr(auth_router, "_send_verification_code", lambda db, user: True)
    sleep_calls = _record_sleep(monkeypatch)
    _make_verified_user(testing_session, "r21-resend@example.com")
    r = c.post("/api/auth/resend-code", json={"email": "r21-resend@example.com"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert len(sleep_calls) == 1, f"已注册分支应 sleep 一次，实际 {sleep_calls}"
    # 补足量 = uniform(1.5,3.5) - elapsed，允许 elapsed 带来的毫秒级浮动
    assert 1.4 <= sleep_calls[0] <= 3.5, f"补足量应落在配置区间内：{sleep_calls}"


def test_r21_resend_email_failed_padded_before_raise(client, monkeypatch):
    """发信失败 500 分支：垫完再抛，sleep 仍被调用一次。"""
    c, testing_session = client
    monkeypatch.setattr(auth_router, "UNKNOWN_EMAIL_DELAY_RANGE", (1.5, 3.5))
    monkeypatch.setattr(auth_router, "_send_verification_code", lambda db, user: False)
    sleep_calls = _record_sleep(monkeypatch)
    _make_verified_user(testing_session, "r21-resend-fail@example.com")
    r = c.post("/api/auth/resend-code", json={"email": "r21-resend-fail@example.com"})
    assert r.status_code == 500, f"发信失败应 500：{r.status_code} {r.text}"
    assert r.json().get("code") == "email_failed"
    assert len(sleep_calls) == 1, f"500 分支应先垫再抛，sleep 一次，实际 {sleep_calls}"
    assert 1.4 <= sleep_calls[0] <= 3.5, f"补足量应落在配置区间内：{sleep_calls}"
