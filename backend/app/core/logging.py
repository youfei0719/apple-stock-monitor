"""结构化 JSON 日志：控制台 + logs/ 按日轮转文件。"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler

import structlog

_configured = False


def configure_logging(log_dir: str = "logs", filename: str | None = None) -> structlog.stdlib.BoundLogger:
    global _configured
    os.makedirs(log_dir, exist_ok=True)
    if filename is None:
        # R7 日志方案(a)：API 进程与 engine 进程各写各的日志文件。
        # TimedRotatingFileHandler 非多进程安全：双进程同时持有 app.log，
        # 午夜 rollover 时会竞态（互相截断/覆盖对方的轮转文件）。
        # systemd unit 通过 STOCKMON_LOG_NAME 传入进程身份
        # （stockmon-api.service → stockmon-api.log，
        #  stockmon-engine.service → stockmon-engine.log）；
        # 本地直跑未设置时回退到 app.log（与旧行为一致）。
        filename = os.environ.get("STOCKMON_LOG_NAME", "app.log")

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            timestamper,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not _configured:
        fmt = logging.Formatter("%(message)s")
        file_handler = TimedRotatingFileHandler(
            os.path.join(log_dir, filename),
            when="midnight",
            interval=1,
            backupCount=30,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(fmt)
        root.addHandler(stream_handler)
        _configured = True

    return structlog.get_logger("stockmon")


def get_logger(name: str = "stockmon") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
