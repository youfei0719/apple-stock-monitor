"""round2: pending_tier/quota_reset_at/email_verified/part_number/consecutive_failures

Revision ID: 58c7fb12b7d0
Revises: 0001_initial
Create Date: 2026-10-08 18:12:38.056332

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '58c7fb12b7d0'
down_revision: str | None = '0001_initial'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 只含 round2 新增列；api_hits 的 alter 系 autogenerate 伪 diff（sqlite 不支持），已剔除
    op.add_column(
        'monitor_tasks',
        sa.Column('consecutive_failures', sa.Integer(), nullable=False, server_default="0"))
    op.add_column('notifications', sa.Column('part_number', sa.String(length=64), nullable=True))
    op.add_column('users', sa.Column('pending_tier', sa.String(length=32), nullable=True))
    op.add_column('users', sa.Column('quota_reset_at', sa.DateTime(), nullable=True))
    op.add_column(
        'users',
        sa.Column('email_verified', sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column('users', 'email_verified')
    op.drop_column('users', 'quota_reset_at')
    op.drop_column('users', 'pending_tier')
    op.drop_column('notifications', 'part_number')
    op.drop_column('monitor_tasks', 'consecutive_failures')
