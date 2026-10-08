"""通知：链路测试、通知历史。"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.errors import APIError
from app.core.db import get_db
from app.models.models import Notification, User
from app.schemas import NotifyTestIn
from app.services.notifier import Notifier

router = APIRouter(tags=["notify"])

# 通知链路测试：每用户每天最多 5 次（防滥用刷外部渠道）
NOTIFY_TEST_DAILY_LIMIT = 5


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
            "status": n.status,
            "error": n.error,
            "created_at": n.created_at.isoformat() + "Z",
        }
        for n in rows
    ]
