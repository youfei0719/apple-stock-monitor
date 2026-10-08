"""round4 后端修复：quota_usage.period 列宽 7 -> 10。

R4-P2：QuotaUsage.period 实际写入 10 字符（配额锚点日期 YYYY-MM-DD，
见 engine.quota_period_key），列定义 String(7) 与实际不符。
SQLite 不强制 varchar 长度所以线上没炸，但切 Postgres 会炸；
这里把列宽修正为 10（SQLite 下走 batch 重建表）。

Revision ID: a9b8c7d6e5f4
Revises: f7a8b9c0d1e2
Create Date: 2026-10-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a9b8c7d6e5f4'
down_revision: str | None = 'f7a8b9c0d1e2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("quota_usage") as batch:
        batch.alter_column(
            "period",
            existing_type=sa.String(7),
            type_=sa.String(10),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("quota_usage") as batch:
        batch.alter_column(
            "period",
            existing_type=sa.String(10),
            type_=sa.String(7),
            existing_nullable=False,
        )
