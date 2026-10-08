"""initial: 全部表

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("tier", sa.String(32), nullable=False, server_default="free"),
        sa.Column("tier_expires_at", sa.DateTime(), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("totp_secret", sa.String(64), nullable=True),
        sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False),
        sa.Column("token_digest", sa.String(128), nullable=False),
        sa.Column("totp_verified", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_token_digest", "sessions", ["token_digest"], unique=True)

    op.create_table(
        "monitor_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True),
        sa.Column("device_id", sa.String(128), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("task_group", sa.String(128), nullable=False, server_default=""),
        sa.Column("category", sa.String(32), nullable=False, server_default="iphone"),
        sa.Column("part_number", sa.String(64), nullable=False),
        sa.Column("product_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("color", sa.String(64), nullable=False, server_default=""),
        sa.Column("capacity", sa.String(64), nullable=False, server_default=""),
        sa.Column("store_numbers", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("stores", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("mode", sa.String(16), nullable=False, server_default="instant"),
        sa.Column("repeat_interval_sec", sa.Integer(), nullable=True),
        sa.Column("channels", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_polled_at", sa.DateTime(), nullable=True),
        sa.Column("last_poll_ok", sa.Boolean(), nullable=True),
        sa.Column("last_poll_ms", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_monitor_tasks_user_id", "monitor_tasks", ["user_id"])
    op.create_index("ix_monitor_tasks_device_id", "monitor_tasks", ["device_id"])
    op.create_index("ix_monitor_tasks_part_number", "monitor_tasks", ["part_number"])

    op.create_table(
        "stock_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("monitor_tasks.id", ondelete="CASCADE"),
            nullable=False),
        sa.Column("store_number", sa.String(16), nullable=False),
        sa.Column("part_number", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("prev_known", sa.String(16), nullable=True),
        sa.Column("pickup_display", sa.String(32), nullable=True),
        sa.Column("store_pick_eligible", sa.Boolean(), nullable=True),
        sa.Column("pickup_search_quote", sa.String(512), nullable=True),
        sa.Column("confirmed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_event_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("task_id", "store_number", "part_number"),
    )
    op.create_index("ix_stock_states_task_id", "stock_states", ["task_id"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("monitor_tasks.id", ondelete="SET NULL"),
            nullable=True),
        sa.Column("kind", sa.String(32), nullable=False, server_default="stock_alert"),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("target", sa.String(512), nullable=False, server_default=""),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("link", sa.String(1024), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="sent"),
        sa.Column("error", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_task_id", "notifications", ["task_id"])

    op.create_table(
        "quota_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("push_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "period"),
    )
    op.create_index("ix_quota_usage_user_id", "quota_usage", ["user_id"])

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True),
        sa.Column("order_id", sa.String(128), nullable=False),
        sa.Column("plan", sa.String(128), nullable=False, server_default=""),
        sa.Column("amount_cny", sa.Float(), nullable=False, server_default="0"),
        sa.Column("tier_from", sa.String(32), nullable=False, server_default=""),
        sa.Column("tier_to", sa.String(32), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="paid"),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_payments_user_id", "payments", ["user_id"])
    op.create_index("ix_payments_order_id", "payments", ["order_id"], unique=True)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("admin_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False, server_default=""),
        sa.Column("target_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("detail", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "system_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_system_config_key", "system_config", ["key"], unique=True)

    op.create_table(
        "api_hits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("path", sa.String(256), nullable=False),
        sa.Column("ip_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_api_hits_path", "api_hits", ["path"])
    op.create_index("ix_api_hits_ip_hash", "api_hits", ["ip_hash"])
    op.create_index("ix_api_hits_created_at", "api_hits", ["created_at"])


def downgrade() -> None:
    for table, indexes in [
        ("api_hits", ["ix_api_hits_path", "ix_api_hits_ip_hash", "ix_api_hits_created_at"]),
        ("system_config", ["ix_system_config_key"]),
        ("audit_logs", []),
        ("payments", ["ix_payments_user_id", "ix_payments_order_id"]),
        ("quota_usage", ["ix_quota_usage_user_id"]),
        ("notifications", ["ix_notifications_user_id", "ix_notifications_task_id"]),
        ("stock_states", ["ix_stock_states_task_id"]),
        (
            "monitor_tasks",
            ["ix_monitor_tasks_user_id",
             "ix_monitor_tasks_device_id",
             "ix_monitor_tasks_part_number"],
        ),
        ("sessions", ["ix_sessions_user_id", "ix_sessions_token_digest"]),
        ("users", ["ix_users_email"]),
    ]:
        for ix in indexes:
            op.drop_index(ix, table_name=table)
        op.drop_table(table)
