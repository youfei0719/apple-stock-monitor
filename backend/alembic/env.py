"""Alembic env：从 .env 读 DATABASE_URL；metadata 来自 app.models。"""

import os
import sys

from alembic import context
from sqlalchemy import engine_from_config, event, pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings  # noqa: E402
from app.core.db import Base  # noqa: E402
import app.models  # noqa: E402,F401  (注册全部模型)

config = context.config
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    # R4-P0-2：迁移连接同样注册 WAL + busy_timeout（与 app/core/db.py 一致）。
    # deploy.sh 在重启服务之前跑 alembic upgrade head，旧 engine/API 进程
    # 可能还持有写锁；无 busy_timeout 时迁移直接 database is locked 中断部署。
    if str(connectable.url).startswith("sqlite"):

        @event.listens_for(connectable, "connect")
        def _alembic_sqlite_pragmas(dbapi_conn, _conn_record):
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
            finally:
                cursor.close()

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
