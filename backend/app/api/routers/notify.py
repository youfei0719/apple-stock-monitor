"""通知：链路测试、通知历史、通道健康。"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.errors import APIError
from app.core.db import get_db
from app.models.models import MonitorTask, Notification, User
from app.schemas import NotifyTestIn
from app.services.notifier import Notifier

router = APIRouter(tags=["notify"])

# 通知链路测试：每用户每天最多 5 次（防滥用刷外部渠道）
NOTIFY_TEST_DAILY_LIMIT = 5

# 通道健康展示的五通道（sms 未实现，不展示）
CHANNEL_META: list[tuple[str, str]] = [
    ("bark", "Bark"),
    ("wecom", "企微"),
    ("dingtalk", "钉钉"),
    ("feishu", "飞书"),
    ("email", "邮件"),
]


@router.post("/notify/test")
def notify_test(
    data: NotifyTestIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if data.channel not in ("bark", "wecom", "dingtalk", "feishu", "email", "sms"):
        raise APIError(400, "channel 非法", "bad_channel")
    if not data.target.strip():
        raise APIError(400, "target 不能为空", "bad_target")
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    used_today = db.execute(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.user_id == user.id,
            Notification.kind == "test",
            Notification.created_at >= today_start,
        )
    ).scalar()
    if used_today >= NOTIFY_TEST_DAILY_LIMIT:
        raise APIError(429, "通知链路测试每天最多 5 次", "test_limited")
    n = Notifier(db).test_channel(data.channel, data.target.strip(), user_id=user.id)
    return {
        "ok": n.status == "sent",
        "status": n.status,
        "error": n.error,
        "notification_id": n.id,
    }


@router.get("/notify/channels/health")
def channels_health(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """通道健康（对标差距-4）：各通道近 7 天成功率 + 最后失败原因。

    configured 为近似口径：用户任务里配过该通道，或近 30 天有该通道的通知
    记录（测试/发送均可），见 configured_basis 字段诚实标注。
    success_rate_7d 只统计 sent/failed（skipped 为档位拦截，不计入通道健康）。
    """
    now = datetime.utcnow()
    since_7d = now - timedelta(days=7)
    since_30d = now - timedelta(days=30)

    # 任务配置维度：用户任务 channels 里实际填过的通道
    task_channels: set[str] = set()
    tasks = db.execute(select(MonitorTask).where(MonitorTask.user_id == user.id)).scalars().all()
    for t in tasks:
        ch = t.channels or {}
        if ch.get("bark_key"):
            task_channels.add("bark")
        for wh in ch.get("webhooks") or []:
            if isinstance(wh, dict) and wh.get("platform"):
                task_channels.add(str(wh["platform"]))
        if ch.get("email"):
            task_channels.add("email")

    rows_30d = (
        db.execute(
            select(Notification).where(
                Notification.user_id == user.id,
                Notification.created_at >= since_30d,
            )
        )
        .scalars()
        .all()
    )
    recent_channels = {n.channel for n in rows_30d}

    channels = []
    for key, name in CHANNEL_META:
        recs_7d = [n for n in rows_30d if n.channel == key and n.created_at >= since_7d]
        sent = sum(1 for n in recs_7d if n.status == "sent")
        failed = sum(1 for n in recs_7d if n.status == "failed")
        fails = [n for n in recs_7d if n.status == "failed"]
        last_fail = max(fails, key=lambda n: n.created_at) if fails else None
        channels.append(
            {
                "key": key,
                "name": name,
                "configured": key in task_channels or key in recent_channels,
                "configured_basis": "task_config_or_recent_30d_activity",
                "success_rate_7d": round(sent / (sent + failed), 4) if (sent + failed) else None,
                "last_failure_at": last_fail.created_at.isoformat() + "Z" if last_fail else None,
                "last_failure_reason": last_fail.error if last_fail else None,
            }
        )
    return {"channels": channels}


@router.get("/notifications")
def list_notifications(
    task_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = (
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(desc(Notification.created_at))
        .limit(limit)
    )
    if task_id is not None:
        q = q.where(Notification.task_id == task_id)
    rows = db.execute(q).scalars().all()
    return [
        {
            "id": n.id,
            "task_id": n.task_id,
            "kind": n.kind,
            "channel": n.channel,
            "target": n.target,
            "title": n.title,
            "body": n.body,
            "link": n.link,
            "part_number": n.part_number,
            "status": n.status,
            "error": n.error,
            "created_at": n.created_at.isoformat() + "Z",
        }
        for n in rows
    ]
