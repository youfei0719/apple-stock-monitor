"""round6 后端修复：通知表复合索引 + 任务暂停原因列 + 幂等键表。

- notifications(status, retry_at)：engine._retry_pending_notifications 的查询条件
- notifications(kind, created_at)：lifecycle 按天去重查询的查询条件
- monitor_tasks.paused_reason：暂停原因（R6-I9），认领 device 任务时只有
  quota_exhausted 会自动恢复
- idempotency_records：客户端幂等键表（R6-P2-10），(scope, key) 唯一

Revision ID: c9d4e5f6a7b8
Revises: b8c7d6e5f4a3
Create Date: 2026-10-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c9d4e5f6a7b8'
down_revision: str | None = 'b8c7d6e5f4a3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_notifications_status_retry_at",
        "notifications",
        ["status", "retry_at"],
    )
    op.create_index(
        "ix_notifications_kind_created_at",
        "notifications",
        ["kind", "created_at"],
    )
    op.add_column(
        "monitor_tasks",
        sa.Column("paused_reason", sa.String(64), nullable=True),
    )
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope", sa.String(128), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("task_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("scope", "key", name="uq_idempotency_scope_key"),
    )


def downgrade() -> None:
    op.drop_table("idempotency_records")
    op.drop_column("monitor_tasks", "paused_reason")
    op.drop_index("ix_notifications_kind_created_at", table_name="notifications")
    op.drop_index("ix_notifications_status_retry_at", table_name="notifications")
