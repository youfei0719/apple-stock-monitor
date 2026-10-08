"""FastAPI 入口：挂载路由、健康检查、限流中间件、引擎生命周期。"""

import asyncio
import hashlib
import threading
import time
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
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
from app.core.timeutil import utcnow
from app.models.models import ApiHit, User
from app.services.engine import engine, read_engine_status

configure_logging()
log = get_logger("main")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("app_startup", env=settings.APP_ENV)
    # prod 硬门槛：支付验签为 fail-closed，无 token / 无 plan_id 时拒绝启动，
    # 防止回调接口在未配置签名的情况下对外放行。
    if settings.is_prod:
        required = {
            "AFDIAN_TOKEN": settings.AFDIAN_TOKEN,
            "AFDIAN_PLAN_STANDARD": settings.AFDIAN_PLAN_STANDARD,
            "AFDIAN_PLAN_PRO": settings.AFDIAN_PLAN_PRO,
        }
        missing = [k for k, v in required.items() if not (v or "").strip()]
        if missing:
            raise RuntimeError(f"prod 启动拒绝：缺少必需配置 {missing}")
        # D5：prod 无 SMTP 会导致注册验证码发不出去、用户建好却永远收不到码
        # （403 email_unverified 死胡同）；缺配置直接拒绝启动。
        smtp_required = {
            "SMTP_HOST": settings.SMTP_HOST,
            "SMTP_USER": settings.SMTP_USER,
            "SMTP_PASSWORD": settings.SMTP_PASSWORD,
        }
        smtp_missing = [k for k, v in smtp_required.items() if not (v or "").strip()]
        if smtp_missing:
            raise RuntimeError(f"prod 启动拒绝：缺少必需邮件配置 {smtp_missing}")
    _bootstrap_admin()
    # D1：引擎默认不跑（ENGINE_ENABLED=false），由独立 stockmon-engine.service
    # 进程运行（ENGINE_ENABLED=true）。API 进程内的 engine 实例不再启动。
    if settings.ENGINE_ENABLED:
        await engine.start()
    else:
        log.info("engine_skipped_in_api", reason="ENGINE_ENABLED=false")
    yield
    if settings.ENGINE_ENABLED:
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
        # R6-D3：bootstrap 管理员给远未来到期（+10 年）——否则 5 分钟内就被
        # membership_sweep 按"付费到期"降回 free；sweep 侧同时排除管理员（双保险）
        user.tier_expires_at = utcnow() + timedelta(days=3650)
        db.commit()
        # R5-B-N4：管理员邮箱不明文打日志，只记 sha256 前 8 位（可关联不泄露）
        email_hash = hashlib.sha256(email.encode()).hexdigest()[:8]
        log.info("admin_bootstrapped", email_hash=email_hash)
    except Exception as e:
        db.rollback()
        log.warning("admin_bootstrap_failed", error=str(e))
    finally:
        db.close()


app = FastAPI(title="Apple Stock Monitor", version=settings.APP_VERSION, lifespan=lifespan)

# CORS：只允许前端域名，禁止 "*"
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        # 轻量访问统计（后台流量看板用）；失败不影响主流程。
        # R6-P2-15：内存批量刷盘——每请求一次 INSERT+commit 写放大太大；
        # 缓冲 200 条或 60 秒刷一次，重启丢少量统计可接受（统计非关键数据）。
        _buffer_api_hit(path[:256], hashlib.sha256(ip.encode()).hexdigest()[:32])
    return await call_next(request)


# ---- api_hits 内存批量刷盘（R6-P2-15） ----
_api_hit_buffer: list[tuple[str, str]] = []
_api_hit_lock = threading.Lock()
_api_hit_last_flush = 0.0
API_HIT_BATCH_SIZE = 200
API_HIT_FLUSH_SEC = 60


def _buffer_api_hit(path: str, ip_hash: str) -> None:
    global _api_hit_last_flush
    do_flush = False
    with _api_hit_lock:
        _api_hit_buffer.append((path, ip_hash))
        if len(_api_hit_buffer) >= API_HIT_BATCH_SIZE or (
            _api_hit_buffer and time.time() - _api_hit_last_flush >= API_HIT_FLUSH_SEC
        ):
            do_flush = True
    if do_flush:
        # R13-P3-9：刷盘是同步 DB I/O，不能在事件循环里直接跑（会阻塞所有
        # 并发请求）；丢到线程池执行。非 async 上下文调用时（理论上不存在，
        # 只有上面的 async 中间件会调）降级为直接执行。
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _flush_api_hits()
        else:
            loop.run_in_executor(None, _flush_api_hits)


def _flush_api_hits() -> None:
    """把缓冲的 api_hits 批量入库。"""
    global _api_hit_last_flush
    with _api_hit_lock:
        batch = _api_hit_buffer
        _api_hit_buffer.clear()
        _api_hit_last_flush = time.time()
    if not batch:
        return
    try:
        db = SessionLocal()
        try:
            db.bulk_insert_mappings(
                ApiHit, [{"path": p, "ip_hash": h} for p, h in batch]
            )
            db.commit()
        finally:
            db.close()
    except Exception as e:
        log.warning("api_hit_flush_failed", error=str(e), dropped=len(batch))


@app.get("/healthz")
def healthz():
    # D1：API 进程不跑引擎，引擎状态读独立 engine 进程每 tick 写进 DB 的心跳，
    # 不再读本进程内存（恒为 stopped）。
    # R4-P2：DB 挂时不再 200，返回 503（deploy 健康检查据此判失败）。
    db_ok = True
    engine_state = "stopped"
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            engine_state = "running" if read_engine_status(db)["running"] else "stopped"
        finally:
            db.close()
    except Exception as e:
        db_ok = False
        log.warning("healthz_db_failed", error=str(e))
    payload = {
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
        "engine": engine_state,
        "engine_in_api": settings.ENGINE_ENABLED,
        "version": settings.APP_VERSION,
    }
    if not db_ok:
        return JSONResponse(status_code=503, content=payload)
    return payload


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
