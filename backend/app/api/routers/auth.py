"""认证 / 用户：注册、登录（含失败锁 IP）、登出、me、改密、TOTP、邮箱验证。"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Cookie, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, update
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
from app.models.models import MonitorTask, Notification, QuotaUsage, SystemConfig, User
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
from app.services.lifecycle import converge_task_limit
from app.services.notifier import send_email

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger("auth")
settings = get_settings()

# 邮箱验证码：10 分钟有效，存 system_config（key=email_code:<email>）
EMAIL_CODE_TTL_MIN = 10
EMAIL_CODE_KEY_PREFIX = "email_code:"

# ---- 尝试次数锁定（R6-P2-7/P2-8）：5 次失败锁定 15 分钟 ----
ATTEMPT_FAIL_LIMIT = 5
ATTEMPT_LOCK_MINUTES = 15


def _attempt_key(prefix: str, ident: str) -> str:
    return f"{prefix}:{ident}"


def _check_attempt_lock(db: Session, prefix: str, ident: str, what: str) -> None:
    """检查是否被锁定；锁定中则 429。"""
    row = db.execute(
        select(SystemConfig).where(SystemConfig.key == _attempt_key(prefix, ident))
    ).scalar_one_or_none()
    if row:
        locked_until = (row.value or {}).get("locked_until")
        if locked_until:
            try:
                until = datetime.fromisoformat(str(locked_until).rstrip("Z"))
            except ValueError:
                until = None
            if until is not None and datetime.utcnow() < until:
                raise APIError(429, f"{what}尝试次数过多，请 15 分钟后再试", "attempt_locked")


def _record_attempt_fail(db: Session, prefix: str, ident: str) -> None:
    """记一次失败；达到上限则锁定。"""
    key = _attempt_key(prefix, ident)
    row = db.execute(select(SystemConfig).where(SystemConfig.key == key)).scalar_one_or_none()
    fails = int((row.value or {}).get("fails", 0)) + 1 if row else 1
    value = {"fails": fails}
    if fails >= ATTEMPT_FAIL_LIMIT:
        value["locked_until"] = (
            datetime.utcnow() + timedelta(minutes=ATTEMPT_LOCK_MINUTES)
        ).isoformat() + "Z"
    if row:
        row.value = value
        db.add(row)
    else:
        db.add(SystemConfig(key=key, value=value))
    db.commit()


def _clear_attempt_lock(db: Session, prefix: str, ident: str) -> None:
    row = db.execute(
        select(SystemConfig).where(SystemConfig.key == _attempt_key(prefix, ident))
    ).scalar_one_or_none()
    if row:
        db.delete(row)
        db.commit()


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


def _strip_channel_secrets(channels: dict | None) -> tuple[dict, bool]:
    """R6-I4：剥离渠道密钥。X-Device-Id 是客户端可伪造的值，不能凭它过户
    含密钥的任务——bark_key / email / webhook URL 全部清空，webhooks 置空
    数组。返回 (新 channels, 是否剥离过密钥)。"""
    ch = dict(channels or {})
    had_secrets = False
    for k in ("bark_key", "email"):
        if ch.pop(k, None):
            had_secrets = True
    webhooks = ch.get("webhooks") or []
    if webhooks:
        had_secrets = True
    ch["webhooks"] = []
    return ch, had_secrets


def _claim_device_tasks(db: Session, request: Request, user: User) -> dict:
    """登录/注册成功后，把同 X-Device-Id 的匿名任务迁移绑定到新登录用户（断裂-10）。

    R5-F-N4：认领时同步把这些任务下 user_id 为空的通知一并过户——否则历史
    /通知仍挂在匿名名下，用户在「历史」里看不到认领前发出的到货通知。
    R6-I4：认领时剥离渠道密钥（见 _strip_channel_secrets），返回提示让用户
    重新配置。
    R6-I9：因配额耗尽自动暂停的任务（paused_reason == "quota_exhausted"）
    自动恢复——用户已注册/登录，有新的配额周期可用；其他原因暂停的不动。
    R10-P2-6：认领后做任务数冲突检测——用户已在档位上限时，认领会把总数
    推超上限（匿名 trial 限额 1，最多超 1 个，影响小但真实）。超限时按
    converge_task_limit 口径暂停超出的任务（reason="tier_limit"，升级后
    I14 可恢复），返回 paused_over_limit 供提示文案使用。

    返回 {"claimed": n, "secrets_stripped": m, "resumed": k, "paused_over_limit": j}。
    """
    device_id = request.headers.get("x-device-id")
    if not device_id:
        return {"claimed": 0, "secrets_stripped": 0, "resumed": 0, "paused_over_limit": 0}
    rows = (
        db.execute(
            select(MonitorTask).where(
                MonitorTask.user_id.is_(None), MonitorTask.device_id == device_id
            )
        )
        .scalars()
        .all()
    )
    secrets_stripped = 0
    resumed = 0
    for t in rows:
        t.user_id = user.id
        # N10：迁移后清空 device_id，避免同 device_id 重复认领/脏数据残留
        t.device_id = None
        t.channels, had = _strip_channel_secrets(t.channels)
        if had:
            secrets_stripped += 1
        if t.paused and getattr(t, "paused_reason", None) == "quota_exhausted":
            t.paused = False
            t.paused_reason = None
            resumed += 1
        db.add(t)
    paused_over_limit = 0
    if rows:
        db.execute(
            update(Notification)
            .where(
                Notification.task_id.in_([t.id for t in rows]),
                Notification.user_id.is_(None),
            )
            .values(user_id=user.id)
        )
        # R10-P2-6：认领把任务数推超档位上限时立即收敛（与 refund/patch_user
        # 降档同口径），别让刚认领的任务静默吃超限配额
        paused_over_limit = len(converge_task_limit(db, user, reason="tier_limit"))
        log.info("device_tasks_claimed", user_id=user.id, device_id=device_id, count=len(rows))
    return {
        "claimed": len(rows),
        "secrets_stripped": secrets_stripped,
        "resumed": resumed,
        "paused_over_limit": paused_over_limit,
    }


def _claim_notice(info: dict) -> str | None:
    """把认领结果拼成给用户的提示文案（R6-I4/I9；R10-P2-6 补超限暂停提示）。"""
    parts = []
    if info["claimed"]:
        parts.append(f"已认领 {info['claimed']} 个匿名任务")
    if info["secrets_stripped"]:
        parts.append("认领任务的通知渠道密钥已清空，请重新配置通知渠道")
    if info["resumed"]:
        parts.append(f"{info['resumed']} 个因配额耗尽暂停的任务已自动恢复")
    if info.get("paused_over_limit"):
        parts.append(
            f"任务数超出当前档位上限，{info['paused_over_limit']} 个任务已自动暂停"
        )
    return "；".join(parts) if parts else None


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
    log.info(
        "user_registered",
        user_id=user.id,
        claimed_tasks=claimed["claimed"],
        email_sent=email_sent,
    )
    return UserOut(
        id=user.id,
        email=user.email,
        tier=user.tier,
        totp_enabled=user.totp_enabled,
        notice=_claim_notice(claimed),
        email_sent=email_sent,
    )


@router.post("/verify-email")
def verify_email(data: VerifyEmailIn, db: Session = Depends(get_db)):
    """邮箱验证：校验 6 位验证码（10 分钟有效），通过后置 email_verified=True。"""
    email = data.email.strip().lower()
    # R6-P2-7：按 email 记失败次数，5 次锁定 15 分钟（防验证码低速爆破）
    _check_attempt_lock(db, "verify_email_fail", email, "验证码")
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user:
        # R4-P2 防用户枚举：未知邮箱与"验证码错误"返回完全相同的 400，
        # 不可通过 404/400 差异探测邮箱是否注册
        _record_attempt_fail(db, "verify_email_fail", email)
        raise APIError(400, "验证码错误", "bad_code")
    if user.email_verified:
        return {"ok": True, "already": True}
    key = _email_code_key(user.email)
    row = db.execute(select(SystemConfig).where(SystemConfig.key == key)).scalar_one_or_none()
    # R6-P2-18：常量时间比较，防时序侧信道
    stored = (row.value or {}).get("code") if row else None
    if not row or not hmac.compare_digest(str(stored or ""), data.code):
        _record_attempt_fail(db, "verify_email_fail", email)
        raise APIError(400, "验证码错误", "bad_code")
    expires_at = datetime.fromisoformat((row.value or {}).get("expires_at", "").rstrip("Z"))
    if datetime.utcnow() > expires_at:
        # R4-P1-D6：过期文案指引"重新发送"，而不是"重新注册"
        # （该邮箱已注册，重新注册必 400 email_taken，用户会撞墙）
        _record_attempt_fail(db, "verify_email_fail", email)
        raise APIError(400, "验证码已过期，请点击重新发送获取新验证码", "code_expired")
    user.email_verified = True
    db.add(user)
    db.delete(row)
    _clear_attempt_lock(db, "verify_email_fail", email)
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
    # R6-P2-9：IP 维度总量限流（每小时 20 次），防用大量邮箱地址刷邮件
    if not check_rate_limit(f"resend_code_ip:{_client_ip(request)}", limit=20, window_sec=3600):
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
    if not user:
        fails = record_login_failure(ip)
        # R4-P1-B3：失败日志不打邮箱明文（撞库时会攒出真实邮箱清单），只记哈希
        email_hash = hashlib.sha256(email.encode()).hexdigest()[:16]
        log.warning("login_failed", email_hash=email_hash, ip=ip, fails=fails)
        raise APIError(401, "邮箱或密码错误", "bad_credentials")
    # R9-O3：未验证账号不论密码对错一律 403 email_unverified。原来的顺序是
    # "先验密码再判验证状态"：密码正确但未验证 → 403，密码错误 → 401，
    # 401/403 的差异可预言密码正确性。现在验证状态先行——正常流程不变
    # （密码正确但未验证本来就是 403），且不记录登录失败（未验证账号的
    # 密码本来就不可用，不应计入 IP 锁定）。
    if not user.email_verified:
        # R10-P2-4：计时侧信道加固——未验证分支同样跑一次 verify_password
        #（结果丢弃，仅为消耗与真实校验同量级的 bcrypt 耗时），否则攻击者
        # 可用"未验证+密码正确 → 403 快 / 未验证+密码错误 → 403 慢"的计时
        # 差异预言密码正确性。
        verify_password(data.password, user.password_hash)
        raise APIError(403, "邮箱尚未验证，请先完成邮箱验证", "email_unverified")
    if not verify_password(data.password, user.password_hash):
        fails = record_login_failure(ip)
        # R4-P1-B3：失败日志不打邮箱明文（撞库时会攒出真实邮箱清单），只记哈希
        email_hash = hashlib.sha256(email.encode()).hexdigest()[:16]
        log.warning("login_failed", email_hash=email_hash, ip=ip, fails=fails)
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
    claimed = _claim_device_tasks(db, request, user)
    db.commit()
    _set_session_cookie(response, token)
    log.info("login_ok", user_id=user.id, ip=ip, claimed_tasks=claimed["claimed"])
    return {
        "ok": True,
        "totp_required": bool(user.is_admin and settings.ADMIN_TOTP_REQUIRED),
        "notice": _claim_notice(claimed),
    }


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
    # R5-F-N1：tasks_used 口径统一为全量（含已暂停），与 _check_task_limit、
    # GET /quota 的计数一致（此前这里只计 paused=false，三处口径打架）
    tasks_used_n = db.execute(
        select(func.count()).select_from(MonitorTask).where(MonitorTask.user_id == user.id)
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
    # R6-P2-8：按用户记 TOTP 失败次数，5 次锁定 15 分钟（防低速爆破）
    _check_attempt_lock(db, "totp_fail", str(user.id), "TOTP 验证码")
    if not user.totp_secret or not verify_totp(user.totp_secret, data.code):
        _record_attempt_fail(db, "totp_fail", str(user.id))
        raise APIError(400, "验证码错误", "bad_totp")
    _clear_attempt_lock(db, "totp_fail", str(user.id))
    user.totp_enabled = True
    db.add(user)
    s = get_session(request, db, session_token)
    if s:
        s.totp_verified = True
        db.add(s)
    db.commit()
    log.info("totp_verified", user_id=user.id)
    return {"ok": True, "totp_enabled": True}
