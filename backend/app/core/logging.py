"""结构化 JSON 日志：控制台 + logs/ 按日轮转文件。"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler

import structlog

_configured = False


def configure_logging(log_dir: str = "logs") -> structlog.stdlib.BoundLogger:
    global _configured
    os.makedirs(log_dir, exist_ok=True)

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
            os.path.join(log_dir, "app.log"),
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
