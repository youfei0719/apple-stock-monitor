"""SQLAlchemy 模型。全部时间 UTC。"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _utcnow() -> datetime:
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    tier: Mapped[str] = mapped_column(String(32), default="free", nullable=False)
    tier_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 降级到期生效：用户主动降级时记在这里，到期 sweep 再切换 tier
    pending_tier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 购买日+30天滚动配额周期锚点（R4-P2：实际为 UTC 锚点，展示时转北京时间）
    quota_reset_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    sessions: Mapped[list["Session"]] = relationship(back_populates="user", cascade="all,delete")
    tasks: Mapped[list["MonitorTask"]] = relationship(back_populates="user", cascade="all,delete")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_digest: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    totp_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    user: Mapped["User"] = relationship(back_populates="sessions")


class MonitorTask(Base):
    __tablename__ = "monitor_tasks"
    # R4-P2：engine._due_tasks 每 5 秒按 paused/expires_at 粗筛，加复合索引
    __table_args__ = (Index("ix_monitor_tasks_paused_expires", "paused", "expires_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    device_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    group: Mapped[str] = mapped_column("task_group", String(128), default="", nullable=False)
    category: Mapped[str] = mapped_column(String(32), default="iphone", nullable=False)
    part_number: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    color: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    capacity: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    store_numbers: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    stores: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # [{number,name,city}]
    mode: Mapped[str] = mapped_column(String(16), default="instant", nullable=False)
    repeat_interval_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channels: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 暂停原因（R6-I9）：quota_exhausted / notify_failures / zombie / tier_limit /
    # manual；认领 device 任务时只有 quota_exhausted 会自动恢复
    paused_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 连续通知失败次数；全通道失败+1、全成功清零；>=10 自动暂停（不断裂-6）
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 连续 90 天无货是否自动暂停（僵尸任务清理，断裂-11）；默认开，用户可关
    auto_retire: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_poll_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_poll_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    # R13-P1-1：一键续期去重专用列——仅续期成功时写入；引擎轮询只碰
    # last_polled_at/last_poll_ok/last_poll_ms，不再误触发去重
    last_renewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    user: Mapped["User | None"] = relationship(back_populates="tasks")
    states: Mapped[list["StockState"]] = relationship(back_populates="task", cascade="all,delete")


class StockState(Base):
    """每个 task × 门店 × part 的最新状态（含基线/连续确认计数）。"""

    __tablename__ = "stock_states"
    __table_args__ = (UniqueConstraint("task_id", "store_number", "part_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("monitor_tasks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    store_number: Mapped[str] = mapped_column(String(16), nullable=False)
    part_number: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    prev_known: Mapped[str | None] = mapped_column(String(16), nullable=True)
    pickup_display: Mapped[str | None] = mapped_column(String(32), nullable=True)
    store_pick_eligible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    pickup_search_quote: Mapped[str | None] = mapped_column(String(512), nullable=True)
    confirmed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    task: Mapped["MonitorTask"] = relationship(back_populates="states")


class Notification(Base):
    __tablename__ = "notifications"
    # R6-P2-14：(status, retry_at) 供 _retry_pending_notifications 查询；
    # (kind, created_at) 供 lifecycle 按天去重查询
    __table_args__ = (
        Index("ix_notifications_status_retry_at", "status", "retry_at"),
        Index("ix_notifications_kind_created_at", "kind", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitor_tasks.id", ondelete="SET NULL"), index=True, nullable=True
    )
    kind: Mapped[str] = mapped_column(String(32), default="stock_alert", nullable=False)
    # bark / wecom / dingtalk / feishu / email / sms
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    link: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    # 任务删除后保留的 part_number 快照，避免历史变成"幽灵"通知（不自洽-4）
    part_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="sent", nullable=False)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # 发送失败重试（断裂-5）：retry_count=已重试次数（上限 3，含 tick 内立即重试 1 次）；
    # retry_at=下次重试时间；engine 每 tick 捞 retry_at<=now AND status='failed' 重发
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class QuotaUsage(Base):
    __tablename__ = "quota_usage"
    __table_args__ = (UniqueConstraint("user_id", "period"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    period: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD（配额锚点日期）
    push_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    order_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    plan: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    amount_cny: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    tier_from: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    tier_to: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="paid", nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class SystemConfig(Base):
    """KV 键值：冷却状态、门店目录、产品目录、种子数据等。"""

    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    value: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class ApiHit(Base):
    """轻量访问统计，供后台流量看板聚合（PV/UV）。"""

    __tablename__ = "api_hits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String(256), index=True, nullable=False)
    ip_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)


class IdempotencyRecord(Base):
    """客户端幂等键（R6-P2-10）：任务创建接口接受 Idempotency-Key 请求头。

    (scope, key) 唯一，scope="u:<user_id>" / "d:<device_id>"；
    重复 key 直接返回首次创建的任务，不再走创建流程。
    """

    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("scope", "key", name="uq_idempotency_scope_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(128), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    task_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
