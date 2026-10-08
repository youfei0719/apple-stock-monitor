"""round13 修复：一键续期去重改用专用列。

P1-1：renew_task 原先用 updated_at < 10s 做并发双击去重，但 updated_at 带
onupdate=_utcnow，引擎每轮 poll（写 last_polled_at/last_poll_ok/last_poll_ms）
都会推进它，导致续期被静默吞掉（pro 用户约 87% 概率续期无效，前端还显示
"已续期至"假象）。改用 last_renewed_at 专用列——仅续期成功时写入，引擎轮询
不碰它，去重不再误触发。

Revision ID: e4f5a6b7c8d9
Revises: d2e4f6a8b0c1
Create Date: 2026-10-09

"""

import sqlalchemy as sa
from alembic import op

revision: str = "e4f5a6b7c8d9"
down_revision: str | None = "d2e4f6a8b0c1"


def upgrade() -> None:
    op.add_column(
        "monitor_tasks",
        sa.Column("last_renewed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("monitor_tasks", "last_renewed_at")
