"""round7 后端修复：api_hits.created_at 改为 nullable=False。

与模型对齐：ApiHit.created_at 在 models.py 里是 default=_utcnow 且不可为空
（其他表如 notifications.created_at 均为 nullable=False），0001 迁移里却写成
nullable=True，导致 `alembic check` 报模型/迁移漂移。SQLite 不支持原地 ALTER
COLUMN，必须走 batch_alter_table。

Revision ID: d2e4f6a8b0c1
Revises: c9d4e5f6a7b8
Create Date: 2026-10-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2e4f6a8b0c1"
down_revision: str | None = "c9d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 防御性回填：历史脏数据若有 NULL，改为纪元起点，避免 NOT NULL 约束失败
    op.execute(
        sa.text(
            "UPDATE api_hits SET created_at = '1970-01-01 00:00:00' "
            "WHERE created_at IS NULL"
        )
    )
    # SQLite 必需 batch 模式
    with op.batch_alter_table("api_hits") as batch_op:
        batch_op.alter_column(
            "created_at", existing_type=sa.DateTime(), nullable=False
        )


def downgrade() -> None:
    with op.batch_alter_table("api_hits") as batch_op:
        batch_op.alter_column(
            "created_at", existing_type=sa.DateTime(), nullable=True
        )
