"""认证 / 用户：注册、登录（含失败锁 IP）、登出、me、改密、TOTP。"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Cookie, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import _client_ip, get_current_user, get_session
from app.api.errors import APIError
from app.core.config import get_settings
from app.core.db import get_db
from app.core.logging import get_logger
from app.core.ratelimit import (
    check_rate_limit,
    login_locked,
    record_login_failure,
    record_login_success,
)
from app.core.security import (
    hash_password,
    new_session_token,
    new_totp_secret,
    token_digest,
    totp_provisioning_uri,
    verify_password,
    verify_totp,
)
from app.core.tiers import tier_of
from app.models.models import MonitorTask, QuotaUsage, User
from app.models.models import Session as DbSession
from app.schemas import (
    LoginIn,
    MeOut,
    PasswordChangeIn,
    RegisterIn,
    TotpVerifyIn,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger("auth")
settings = get_settings()


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="session_token",
        value=token,
        max_age=settings.SESSION_EXPIRE_HOURS * 3600,
        httponly=True,
        secure=settings.is_prod,
        samesite="lax",
        path="/",
    )


def _queue_email_verification(user: User) -> None:
    # TODO: 邮箱验证尚未实现。当前注册直接开通账号，未验证邮箱所有权，
    # 不要静默无验证地上线：上线前必须实现验证邮件发送 → 用户点击链接 →
    # 置 user.email_verified=True，并在 /auth/me 返回未验证状态提示。
    # 当前为占位逻辑，仅记日志。
    log.warning("email_verification_not_implemented", user_id=user.id, email=user.email)


@router.post("/register", response_model=UserOut, status_code=201)
def register(data: RegisterIn, request: Request, db: Session = Depends(get_db)):
    ip = _client_ip(request)
    # 注册接口独立限流：5 次/小时/IP，防批量刷号
    if not check_rate_limit(f"register:{ip}", limit=5, window_sec=3600):
        raise APIError(429, "注册过于频繁，请稍后再试", "rate_limited")
    exists = db.execute(select(User).where(User.email == data.email)).scalar_one_or_none()
    if exists:
        raise APIError(400, "邮箱已注册", "email_taken")
    user = User(email=data.email, password_hash=hash_password(data.password), tier="free")
    db.add(user)
    db.commit()
    db.refresh(user)
    _queue_email_verification(user)
    log.info("user_registered", user_id=user.id)
    return UserOut(id=user.id, email=user.email, tier=user.tier, totp_enabled=user.totp_enabled)


@router.post("/login")
def login(data: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = _client_ip(request)
    locked = login_locked(ip)
    if locked > 0:
        raise APIError(429, f"登录失败次数过多，{int(locked)} 秒后重试", "login_locked")
    user = db.execute(select(User).where(User.email == data.email)).scalar_one_or_none()
    if not user or not verify_password(data.password, user.password_hash):
        fails = record_login_failure(ip)
        log.warning("login_failed", email=data.email, ip=ip, fails=fails)
        raise APIError(401, "邮箱或密码错误", "bad_credentials")
    record_login_success(ip)
    token = new_session_token()
    s = DbSession(
        user_id=user.id,
        token_digest=token_digest(token),
        ip=ip,
        user_agent=(request.headers.get("user-agent") or "")[:512],
        expires_at=datetime.utcnow() + timedelta(hours=settings.SESSION_EXPIRE_HOURS),
    )
    db.add(s)
    db.commit()
    _set_session_cookie(response, token)
    log.info("login_ok", user_id=user.id, ip=ip)
    return {"ok": True, "totp_required": bool(user.is_admin and settings.ADMIN_TOTP_REQUIRED)}


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    session_token: str | None = Cookie(default=None),
):
    s = get_session(request, db, session_token)
    if s:
        db.delete(s)
        db.commit()
    response.delete_cookie("session_token", path="/")
    return Response(status_code=204)


def _quota_for(db: Session, user: User) -> dict:
    period = datetime.utcnow().strftime("%Y-%m")
    usage = db.execute(
        select(QuotaUsage).where(QuotaUsage.user_id == user.id, QuotaUsage.period == period)
    ).scalar_one_or_none()
    push_used = usage.push_count if usage else 0
    tier = tier_of(user.tier)
    tasks_used = db.execute(
        select(MonitorTask).where(MonitorTask.user_id == user.id, MonitorTask.paused.is_(False))
    ).scalars()
    tasks_used_n = len(list(tasks_used))
    return {
        "push_used": push_used,
        "push_limit": tier["push_limit"],
        "tasks_used": tasks_used_n,
        "tasks_limit": tier["tasks_limit"],
    }


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return MeOut(
        id=user.id,
        email=user.email,
        tier=user.tier,
        quota=_quota_for(db, user),
        totp_enabled=user.totp_enabled,
    )


# 兼容契约 GET /api/me
me_router = APIRouter(tags=["auth"])


@me_router.get("/me", response_model=MeOut)
def me_alias(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return me(user, db)


@router.patch("/me")
def patch_me(
    data: PasswordChangeIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    session_token: str | None = Cookie(default=None),
):
    if not verify_password(data.old_password, user.password_hash):
        raise APIError(400, "旧密码不正确", "bad_old_password")
    user.password_hash = hash_password(data.password)
    db.add(user)
    # 改密后删除该用户其他 session（当前会话保留），防旧会话继续有效
    if session_token:
        current_digest = token_digest(session_token)
        db.query(DbSession).filter(
            DbSession.user_id == user.id,
            DbSession.token_digest != current_digest,
        ).delete(synchronize_session=False)
    db.commit()
    log.info("password_changed", user_id=user.id)
    return {"ok": True}


@router.post("/totp/setup")
def totp_setup(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.totp_secret:
        user.totp_secret = new_totp_secret()
        db.add(user)
        db.commit()
    return {
        "secret": user.totp_secret,
        "uri": totp_provisioning_uri(user.totp_secret, user.email),
        "enabled": user.totp_enabled,
    }


@router.post("/totp/verify")
def totp_verify(
    data: TotpVerifyIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    session_token: str | None = Cookie(default=None),
):
    if not user.totp_secret or not verify_totp(user.totp_secret, data.code):
        raise APIError(400, "验证码错误", "bad_totp")
    user.totp_enabled = True
    db.add(user)
    s = get_session(request, db, session_token)
    if s:
        s.totp_verified = True
        db.add(s)
    db.commit()
    log.info("totp_verified", user_id=user.id)
    return {"ok": True, "totp_enabled": True}
