"""FastAPI 入口：挂载路由、健康检查、限流中间件、引擎生命周期。"""

import hashlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import _client_ip
from app.api.errors import APIError
from app.api.routers import (
    admin,
    analytics,
    auth,
    catalog,
    guide,
    history,
    notify,
    pay,
    quota,
    stats,
    tasks,
)
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.core.ratelimit import check_rate_limit
from app.core.security import hash_password
from app.models.models import ApiHit, User
from app.services.engine import engine

configure_logging()
log = get_logger("main")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("app_startup", env=settings.APP_ENV)
    _bootstrap_admin()
    await engine.start()
    yield
    await engine.stop()
    log.info("app_shutdown")


def _bootstrap_admin():
    """首个管理员账号：通过 ADMIN_EMAIL/ADMIN_PASSWORD 环境变量 bootstrap。
    仅当库中尚无管理员时创建，避免重复。"""
    email = (settings.ADMIN_EMAIL or "").strip().lower()
    password = settings.ADMIN_PASSWORD or ""
    if not email or not password:
        return
    db = SessionLocal()
    try:
        if db.query(User).filter(User.is_admin.is_(True)).first():
            return
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            user = User(email=email, tier="pro")
            db.add(user)
        user.password_hash = hash_password(password)
        user.is_admin = True
        user.tier = "pro"
        db.commit()
        log.info("admin_bootstrapped", email=email)
    except Exception as e:
        db.rollback()
        log.warning("admin_bootstrap_failed", error=str(e))
    finally:
        db.close()


app = FastAPI(title="Apple Stock Monitor", version=settings.APP_VERSION, lifespan=lifespan)


@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError):
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code}
    )


@app.middleware("http")
async def rate_limit_and_track(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api"):
        ip = _client_ip(request)
        if not check_rate_limit(ip):
            return JSONResponse(
                status_code=429, content={"detail": "请求过于频繁", "code": "rate_limited"}
            )
        # 轻量访问统计（后台流量看板用）；失败不影响主流程
        try:
            db = SessionLocal()
            try:
                db.add(
                    ApiHit(
                        path=path[:256],
                        ip_hash=hashlib.sha256(ip.encode()).hexdigest()[:32],
                    )
                )
                db.commit()
            finally:
                db.close()
        except Exception as e:
            log.warning("api_hit_write_failed", error=str(e))
    return await call_next(request)


@app.get("/healthz")
def healthz():
    db_ok = True
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
    except Exception as e:
        db_ok = False
        log.warning("healthz_db_failed", error=str(e))
    eng = engine.status()
    return {
        "status": "ok",
        "db": db_ok,
        "engine": "running" if eng["running"] else "stopped",
        "version": settings.APP_VERSION,
    }


# /api/me 兼容（契约要求 GET /api/me）
app.include_router(auth.me_router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
app.include_router(catalog.router, prefix="/api")
app.include_router(notify.router, prefix="/api")
app.include_router(history.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(guide.router, prefix="/api")
app.include_router(stats.router, prefix="/api")
app.include_router(quota.router, prefix="/api")
app.include_router(pay.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
