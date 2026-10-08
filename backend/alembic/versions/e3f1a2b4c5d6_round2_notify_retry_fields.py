"""round2 后端修复：notifications.retry_count/retry_at（发送失败重试，断裂-5）；
老用户 email_verified 回填 True（邮箱验证上线前已存在的账号视为已验证，避免被锁死）。

Revision ID: e3f1a2b4c5d6
Revises: 58c7fb12b7d0
Create Date: 2026-10-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e3f1a2b4c5d6'
down_revision: str | None = '58c7fb12b7d0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('notifications', sa.Column('retry_count', sa.Integer(), nullable=False, server_default="0"))
    op.add_column('notifications', sa.Column('retry_at', sa.DateTime(), nullable=True))
    # 老用户回填：邮箱验证功能上线前注册的账号视为已验证
    op.execute(sa.text("UPDATE users SET email_verified = 1"))


def downgrade() -> None:
    op.drop_column('notifications', 'retry_at')
    op.drop_column('notifications', 'retry_count')
