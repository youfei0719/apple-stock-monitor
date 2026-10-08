"""认证 / 用户：注册、登录（含失败锁 IP）、登出、me、改密、TOTP、邮箱验证。"""

import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Cookie, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
from app.core.tiers import effective_tier, effective_tier_of
from app.models.models import MonitorTask, QuotaUsage, SystemConfig, User
from app.models.models import Session as DbSession
from app.schemas import (
    LoginIn,
    MeOut,
    PasswordChangeIn,
    RegisterIn,
    TotpVerifyIn,
    UserOut,
)
from app.services.engine import ensure_quota_anchor, quota_period_key
from app.services.notifier import send_email

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger("auth")
settings = get_settings()

# 邮箱验证码：10 分钟有效，存 system_config（key=email_code:<email>）
EMAIL_CODE_TTL_MIN = 10
EMAIL_CODE_KEY_PREFIX = "email_code:"


class VerifyEmailIn(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


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


def _email_code_key(email: str) -> str:
    return f"{EMAIL_CODE_KEY_PREFIX}{email}"


def _send_verification_code(db: Session, user: User) -> bool:
    """生成 6 位验证码并邮件发送（断裂-22；复用 notifier.send_email）。"""
    code = f"{secrets.randbelow(900000) + 100000:06d}"
    expires_at = datetime.utcnow() + timedelta(minutes=EMAIL_CODE_TTL_MIN)
    key = _email_code_key(user.email)
    row = db.execute(select(SystemConfig).where(SystemConfig.key == key)).scalar_one_or_none()
    value = {"code": code, "expires_at": expires_at.isoformat() + "Z"}
    if row:
        row.value = value
        db.add(row)
    else:
        db.add(SystemConfig(key=key, value=value))
    db.commit()
    try:
        send_email(
            user.email,
            "StockMon 邮箱验证码",
            f"你的邮箱验证码是 {code}，{EMAIL_CODE_TTL_MIN} 分钟内有效。",
        )
        log.info("verify_email_sent", user_id=user.id)
        return True
    except Exception as e:
        log.warning("verify_email_send_failed", user_id=user.id, error=str(e))
        return False


def _claim_device_tasks(db: Session, request: Request, user: User) -> int:
    """登录/注册成功后，把同 X-Device-Id 的匿名任务迁移绑定到新登录用户（断裂-10）。"""
    device_id = request.headers.get("x-device-id")
    if not device_id:
        return 0
    rows = (
        db.execute(
            select(MonitorTask).where(
                MonitorTask.user_id.is_(None), MonitorTask.device_id == device_id
            )
        )
        .scalars()
        .all()
    )
    for t in rows:
        t.user_id = user.id
        # N10：迁移后清空 device_id，避免同 device_id 重复认领/脏数据残留
        t.device_id = None
        db.add(t)
    if rows:
        log.info("device_tasks_claimed", user_id=user.id, device_id=device_id, count=len(rows))
    return len(rows)


@router.post("/register", response_model=UserOut, status_code=201)
def register(data: RegisterIn, request: Request, db: Session = Depends(get_db)):
    ip = _client_ip(request)
    # 注册接口独立限流：5 次/小时/IP，防批量刷号
    if not check_rate_limit(f"register:{ip}", limit=5, window_sec=3600):
        raise APIError(429, "注册过于频繁，请稍后再试", "rate_limited")
    # R4-P1-B1：邮箱统一 strip().lower() 归一化（register/login/verify/resend
    # 四处一致），Foo@x.com 与 foo@x.com 不再注册成两个账号
    email = data.email.strip().lower()
    exists = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if exists:
        raise APIError(400, "邮箱已注册", "email_taken")
    # 注册即初始化配额锚点（注册日=免费锚点，购买日+30天滚动口径）
    user = User(
        email=email,
        password_hash=hash_password(data.password),
        tier="free",
        email_verified=False,
        quota_reset_at=datetime.utcnow() + timedelta(days=30),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # R4-P2：check-then-insert 竞态——并发同邮箱注册时唯一约束兜底
        db.rollback()
        raise APIError(400, "邮箱已注册", "email_taken") from None
    db.refresh(user)
    claimed = _claim_device_tasks(db, request, user)
    db.commit()
    email_sent = _send_verification_code(db, user)
    log.info("user_registered", user_id=user.id, claimed_tasks=claimed, email_sent=email_sent)
    return UserOut(id=user.id, email=user.email, tier=user.tier, totp_enabled=user.totp_enabled)


@router.post("/verify-email")
def verify_email(data: VerifyEmailIn, db: Session = Depends(get_db)):
    """邮箱验证：校验 6 位验证码（10 分钟有效），通过后置 email_verified=True。"""
    email = data.email.strip().lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user:
        # R4-P2 防用户枚举：未知邮箱与"验证码错误"返回完全相同的 400，
        # 不可通过 404/400 差异探测邮箱是否注册
        raise APIError(400, "验证码错误", "bad_code")
    if user.email_verified:
        return {"ok": True, "already": True}
    key = _email_code_key(user.email)
    row = db.execute(select(SystemConfig).where(SystemConfig.key == key)).scalar_one_or_none()
    if not row or (row.value or {}).get("code") != data.code:
        raise APIError(400, "验证码错误", "bad_code")
    expires_at = datetime.fromisoformat((row.value or {}).get("expires_at", "").rstrip("Z"))
    if datetime.utcnow() > expires_at:
        # R4-P1-D6：过期文案指引"重新发送"，而不是"重新注册"
        # （该邮箱已注册，重新注册必 400 email_taken，用户会撞墙）
        raise APIError(400, "验证码已过期，请点击重新发送获取新验证码", "code_expired")
    user.email_verified = True
    db.add(user)
    db.delete(row)
    db.commit()
    log.info("email_verified", user_id=user.id)
    return {"ok": True}


class ResendCodeIn(BaseModel):
    email: EmailStr


@router.post("/resend-code")
def resend_code(data: ResendCodeIn, request: Request, db: Session = Depends(get_db)):
    """重发邮箱验证码（每小时每邮箱限 3 次）。"""
    email = data.email.strip().lower()
    if not check_rate_limit(f"resend_code:{email}", limit=3, window_sec=3600):
        raise APIError(429, "发送过于频繁，请稍后再试", "rate_limited")
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user:
        # R4-P2 防用户枚举：未知邮箱也返回 ok，不可探测邮箱是否注册
        return {"ok": True}
    if user.email_verified:
        return {"ok": True, "already": True}
    ok = _send_verification_code(db, user)
    if not ok:
        raise APIError(500, "邮件发送失败，请稍后重试", "email_failed")
    return {"ok": True}


@router.post("/login")
def login(data: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = _client_ip(request)
    locked = login_locked(ip)
    if locked > 0:
        raise APIError(429, f"登录失败次数过多，{int(locked)} 秒后重试", "login_locked")
    # R4-P1-B1：登录邮箱同样归一化，换大小写登录不再 401
    email = data.email.strip().lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user or not verify_password(data.password, user.password_hash):
        fails = record_login_failure(ip)
        # R4-P1-B3：失败日志不打邮箱明文（撞库时会攒出真实邮箱清单），只记哈希
        email_hash = hashlib.sha256(email.encode()).hexdigest()[:16]
        log.warning("login_failed", email_hash=email_hash, ip=ip, fails=fails)
        raise APIError(401, "邮箱或密码错误", "bad_credentials")
    # 邮箱未验证不许登录（断裂-22；前端据此 code 提示去验证）
    if not user.email_verified:
        raise APIError(403, "邮箱尚未验证，请先完成邮箱验证", "email_unverified")
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
    claimed = _claim_device_tasks(db, request, user)
    db.commit()
    _set_session_cookie(response, token)
    log.info("login_ok", user_id=user.id, ip=ip, claimed_tasks=claimed)
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
    # 有效档位 + 购买日+30天滚动配额周期（断裂-1/不自洽-2）
    ensure_quota_anchor(db, user)
    period = quota_period_key(user)
    usage = db.execute(
        select(QuotaUsage).where(QuotaUsage.user_id == user.id, QuotaUsage.period == period)
    ).scalar_one_or_none()
    push_used = usage.push_count if usage else 0
    info = effective_tier_of(user)
    # R4-P2：用 func.count() 代替 len(list())，避免把全表行拉进 Python
    tasks_used_n = db.execute(
        select(func.count())
        .select_from(MonitorTask)
        .where(MonitorTask.user_id == user.id, MonitorTask.paused.is_(False))
    ).scalar()
    return {
        "push_used": push_used,
        "push_limit": info["push_limit"],
        "tasks_used": tasks_used_n,
        "tasks_limit": info["tasks_limit"],
    }


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # N2：返回有效档位（付费过期按 free 算），与 /quota 的 tier 口径一致，
    # 而不是 user.tier 原始值
    return MeOut(
        id=user.id,
        email=user.email,
        tier=effective_tier(user),
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
