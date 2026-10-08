"""历史与数据：有货事件（活动日志）、放货记录。"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import get_db
from app.core.tiers import effective_tier_of
from app.models.models import MonitorTask, Notification, User

router = APIRouter(prefix="/history", tags=["history"])


def _since(days: int) -> datetime:
    return datetime.utcnow() - timedelta(days=days)


@router.get("/events")
def events(
    part_number: str | None = Query(default=None),
    store: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """有货事件（活动日志）：源自成功发出的到货通知。"""
    q = (
        select(Notification, MonitorTask)
        .join(MonitorTask, Notification.task_id == MonitorTask.id, isouter=True)
        .where(Notification.user_id == user.id)
        .where(Notification.kind == "stock_alert")
        .where(Notification.status == "sent")
        .where(Notification.created_at >= _since(days))
        .order_by(desc(Notification.created_at))
    )
    out = []
    for n, t in db.execute(q).all():
        pn = t.part_number if t else ""
        if part_number and pn != part_number:
            continue
        if store and store not in (n.body or ""):
            continue
        out.append(
            {
                "id": n.id,
                "task_id": n.task_id,
                "part_number": pn,
                "title": n.title,
                "body": n.body,
                "link": n.link,
                "channel": n.channel,
                "created_at": n.created_at.isoformat() + "Z",
            }
        )
    return out


@router.get("/releases")
def releases(
    days: int = Query(default=7, ge=1, le=90),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """放货记录：按天 × part_number 聚合有货事件。"""
    # 断裂-18：档位过期按 free 算；403 已是结构化错误 code="tier_required"，
    # 前端据此渲染升级引导卡片。
    if not effective_tier_of(user)["history"]:
        from app.api.errors import APIError

        raise APIError(403, "完整历史数据需要标准版及以上", "tier_required")
    rows = db.execute(
        select(
            func.date(Notification.created_at).label("day"),
            MonitorTask.part_number,
            func.count().label("events"),
        )
        .join(MonitorTask, Notification.task_id == MonitorTask.id)
        .where(Notification.user_id == user.id)
        .where(Notification.kind == "stock_alert")
        .where(Notification.status == "sent")
        .where(Notification.created_at >= _since(days))
        .group_by(func.date(Notification.created_at), MonitorTask.part_number)
        .order_by(desc("day"))
    ).all()
    return [{"day": str(r.day), "part_number": r.part_number, "events": r.events} for r in rows]
