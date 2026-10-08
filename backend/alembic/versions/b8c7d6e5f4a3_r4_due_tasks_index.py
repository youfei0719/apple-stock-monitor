"""round4 后端修复：monitor_tasks(paused, expires_at) 复合索引。

R4-P2：engine._due_tasks 每 5 秒全表扫 monitor_tasks 做到期/暂停过滤
（Python 里再算 last_polled_at 间隔）。任务量上来后全表扫是固定开销；
加 (paused, expires_at) 复合索引让过滤走索引。说明：last_polled_at 的
间隔判定仍在 Python 做（jitter + tier 间隔逻辑），索引只负责粗筛。

Revision ID: b8c7d6e5f4a3
Revises: a9b8c7d6e5f4
Create Date: 2026-10-09

"""
from collections.abc import Sequence

from alembic import op

revision: str = 'b8c7d6e5f4a3'
down_revision: str | None = 'a9b8c7d6e5f4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_monitor_tasks_paused_expires",
        "monitor_tasks",
        ["paused", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_monitor_tasks_paused_expires", table_name="monitor_tasks")
