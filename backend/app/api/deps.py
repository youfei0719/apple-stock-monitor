"""认证依赖：Cookie 会话；管理员需 TOTP 验证。"""

from fastapi import Cookie, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import APIError
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import token_digest
from app.core.timeutil import utcnow
from app.models.models import Session as DbSession
from app.models.models import User


def _client_ip(request: Request) -> str:
    """客户端真实 IP。

    仅当直连来源是受信代理（TRUSTED_PROXIES）时才信任 X-Forwarded-For，
    否则一律取直连 IP，防止客户端伪造 XFF 绕过 IP 限流。
    默认 TRUSTED_PROXIES 为空，即默认不信任任何 XFF。
    """
    direct = request.client.host if request.client else "unknown"
    xff = request.headers.get("x-forwarded-for")
    if xff:
        trusted = get_settings().trusted_proxies
        if trusted and direct in trusted:
            first = xff.split(",")[0].strip()
            if first:
                return first
    return direct


def get_session(
    request: Request,
    db: Session,
    session_token: str | None,
) -> DbSession | None:
    if not session_token:
        return None
    digest = token_digest(session_token)
    s = db.execute(select(DbSession).where(DbSession.token_digest == digest)).scalar_one_or_none()
    if not s or s.expires_at < utcnow():
        return None
    return s


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    session_token: str | None = Cookie(default=None),
) -> User:
    s = get_session(request, db, session_token)
    if not s:
        raise APIError(401, "未登录", "unauthorized")
    user = db.get(User, s.user_id)
    if not user:
        raise APIError(401, "用户不存在", "unauthorized")
    return user


def get_current_admin(
    request: Request,
    db: Session = Depends(get_db),
    session_token: str | None = Cookie(default=None),
) -> User:
    user = get_current_user(request, db, session_token)
    if not user.is_admin:
        raise APIError(403, "需要管理员权限", "forbidden")
    settings = get_settings()
    if settings.ADMIN_TOTP_REQUIRED:
        s = get_session(request, db, session_token)
        if not s or not s.totp_verified:
            raise APIError(403, "需要 TOTP 二次验证", "totp_required")
    return user


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
    session_token: str | None = Cookie(default=None),
) -> User | None:
    s = get_session(request, db, session_token)
    if not s:
        return None
    return db.get(User, s.user_id)
