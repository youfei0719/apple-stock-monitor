"""应用配置：全部来自环境变量（.env），禁止硬编码密钥/URL/阈值。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- 基础 ---
    APP_ENV: str = "dev"
    APP_SECRET_KEY: str = "change-me-64hex"
    BASE_URL: str = "http://localhost:8000"
    FRONTEND_URL: str = "http://localhost:5173"
    APP_VERSION: str = "0.1.0"

    # --- 数据库 ---
    DATABASE_URL: str = "sqlite:///./data/app.db"

    # --- Apple 接口 ---
    APPLE_PROVIDER_ORDER: str = "pickup-message,fulfillment-messages"
    APPLE_TIMEOUT_SEC: int = 15
    APPLE_MAX_STORES_PER_REQ: int = 10
    APPLE_COOLDOWN_BASE_SEC: int = 60

    # --- 代理池（可选，逗号分隔） ---
    PROXY_POOL: str = ""

    # --- 邮件 ---
    SMTP_HOST: str = ""
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "监控 <noreply@glint.red>"
    SMTP_TLS: int = 1

    # --- 短信（预留） ---
    SMS_PROVIDER: str = ""
    SMS_ACCESS_KEY: str = ""
    SMS_SECRET: str = ""

    # --- 爱发电 ---
    AFDIAN_USER_ID: str = ""
    AFDIAN_TOKEN: str = ""
    # 自家爱发电赞助页 URL（前端付费指引跳转用；默认占位，生产环境在 .env 配真实地址）
    AFDIAN_PAGE_URL: str = "https://afdian.com/a/stockmon"
    # 占位默认值已移除：prod 启动时若为空直接拒绝启动（见 main.lifespan）
    AFDIAN_PLAN_STANDARD: str = ""
    AFDIAN_PLAN_PRO: str = ""

    # --- 安全 ---
    # 受信代理 IP（逗号分隔）。X-Forwarded-For 仅当直连来源是受信代理时才被信任；
    # 为空时一律只取直连 IP，防止客户端伪造 XFF 绕过 IP 限流。
    TRUSTED_PROXIES: str = ""
    SESSION_EXPIRE_HOURS: int = 168
    ADMIN_TOTP_REQUIRED: int = 1
    LOGIN_FAIL_LOCK: int = 5
    LOGIN_LOCK_MINUTES: int = 15
    # 首个管理员账号 bootstrap（首次启动时创建，之后可清空）
    ADMIN_EMAIL: str = ""
    ADMIN_PASSWORD: str = ""

    # --- 监控引擎 ---
    # 引擎开关：API 进程默认 false（独立 stockmon-engine.service 跑引擎），
    # engine service 的环境变量里设为 true。防止 uvicorn 进程 + 独立进程双引擎
    # 重复轮询/重复通知（第三轮审查 D1）。
    ENGINE_ENABLED: bool = False
    ENGINE_POLL_JITTER_SEC: int = 3
    # 注意：引擎为单线程串行 tick（round2 审查不自洽-14：原 ENGINE_MAX_WORKERS
    # 定义了但从未被引用，已删除；pro 的 10s 刷新为尽力目标，tick 超时记 warning）。
    ENGINE_TICK_SEC: int = 5

    # --- Apple 请求预算（反薅，断裂-20）：单用户/分钟上限按有效档位，trial 最严 ---
    MAX_APPLE_REQ_PER_USER_PER_MIN_PRO: int = 30
    MAX_APPLE_REQ_PER_USER_PER_MIN_STANDARD: int = 20
    MAX_APPLE_REQ_PER_USER_PER_MIN_FREE: int = 10
    MAX_APPLE_REQ_PER_USER_PER_MIN_TRIAL: int = 3
    # 全局每分钟上限；超限时 trial/free 先降速（跳过），pro 优先
    GLOBAL_APPLE_REQ_PER_MIN: int = 120

    # --- 会员档位刷新间隔（秒），可在 .env 覆盖 ---
    TIER_INTERVAL_TRIAL: int = 300
    TIER_INTERVAL_FREE: int = 300
    TIER_INTERVAL_STANDARD: int = 60
    TIER_INTERVAL_PRO: int = 30

    @property
    def provider_order(self) -> list[str]:
        return [p.strip() for p in self.APPLE_PROVIDER_ORDER.split(",") if p.strip()]

    @property
    def proxy_list(self) -> list[str]:
        return [p.strip() for p in self.PROXY_POOL.split(",") if p.strip()]

    @property
    def tier_intervals(self) -> dict[str, int]:
        return {
            "trial": self.TIER_INTERVAL_TRIAL,
            "free": self.TIER_INTERVAL_FREE,
            "standard": self.TIER_INTERVAL_STANDARD,
            "pro": self.TIER_INTERVAL_PRO,
        }

    def per_user_req_limit(self, tier: str) -> int:
        """单用户每分钟 Apple 请求上限（按有效档位）。"""
        return {
            "pro": self.MAX_APPLE_REQ_PER_USER_PER_MIN_PRO,
            "standard": self.MAX_APPLE_REQ_PER_USER_PER_MIN_STANDARD,
            "free": self.MAX_APPLE_REQ_PER_USER_PER_MIN_FREE,
            "trial": self.MAX_APPLE_REQ_PER_USER_PER_MIN_TRIAL,
        }.get(tier, self.MAX_APPLE_REQ_PER_USER_PER_MIN_FREE)

    @property
    def trusted_proxies(self) -> list[str]:
        return [p.strip() for p in self.TRUSTED_PROXIES.split(",") if p.strip()]

    @property
    def is_prod(self) -> bool:
        return self.APP_ENV == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
