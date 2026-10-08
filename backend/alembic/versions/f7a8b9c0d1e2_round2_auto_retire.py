"""round2 后端修复：monitor_tasks.auto_retire（连续 90 天无货是否自动暂停，断裂-11）。

Worker B 的 lifecycle.zombie_sweep 通过 HAS_AUTO_RETIRE_COL 自动检测该列，
列加上后其逻辑自动完整启用。默认 True（开）。

Revision ID: f7a8b9c0d1e2
Revises: e3f1a2b4c5d6
Create Date: 2026-10-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f7a8b9c0d1e2'
down_revision: str | None = 'e3f1a2b4c5d6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'monitor_tasks',
        sa.Column('auto_retire', sa.Boolean(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column('monitor_tasks', 'auto_retire')
