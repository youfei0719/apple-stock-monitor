"""数据分析：全国榜单（城市放货排行）、数据分析摘要。"""

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.routers.catalog import sku_name_for_part_number
from app.core.db import get_db
from app.core.timeutil import utcnow
from app.models.models import MonitorTask, Notification, User

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _event_rows(db: Session, user_id: int, days: int):
    # R4-P2：加界（ranking days≤30 / overview days≤90 全量进内存）
    since = utcnow() - timedelta(days=days)
    return db.execute(
        select(Notification, MonitorTask)
        .join(MonitorTask, Notification.task_id == MonitorTask.id, isouter=True)
        .where(Notification.user_id == user_id)
        .where(Notification.kind == "stock_alert")
        .where(Notification.status == "sent")
        .where(Notification.created_at >= since)
        .limit(5000)
    ).all()


def _city_of(task: MonitorTask | None, body: str) -> str:
    if task and task.stores:
        cities = {str(s.get("city", "")) for s in task.stores if s.get("city")}
        cities.discard("")
        if cities:
            return "、".join(sorted(cities))
    return "未知"


@router.get("/ranking")
def ranking(
    days: int = Query(default=1, ge=1, le=30),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """城市放货排行（个人范围）。

    断裂-19 诚实化：当前按该用户自己的有货通知聚合，不是全站榜单；
    响应带 scope="personal"，前端据此展示「我的放货城市分布」而非「全国榜单」。
    全站匿名聚合是长期项，见审查文档对标差距-6。
    """
    counts: dict[str, int] = {}
    for n, t in _event_rows(db, user.id, days):
        city = _city_of(t, n.body or "")
        counts[city] = counts.get(city, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "scope": "personal",
        "ranking": [{"city": city, "events": c} for city, c in ranked],
    }


@router.get("/overview")
def overview(
    days: int = Query(default=7, ge=1, le=90),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = _event_rows(db, user.id, days)
    by_part: dict[str, int] = {}
    by_day: dict[str, int] = {}
    for n, t in rows:
        # R6-I7：part_number 取通知快照列（任务删除后 join 不到 MonitorTask，
        # 只有快照能给出型号；与 history.py 同口径）
        # P1：榜单展示用完整 SKU 名，不裸显 part_number
        pn = n.part_number or (t.part_number if t else "") or "未知"
        label = pn
        # P1：榜单展示用完整 SKU 名，不裸显 part_number。
        # 优先用任务的产品信息，兜底用目录映射。
        sku = ""
        if t and (t.product_name or t.capacity or t.color):
            sku = (t.product_name or "").strip()
            if t.capacity:
                sku += f" {t.capacity}"
            if t.color:
                sku += f" {t.color}"
            sku = sku.strip()
        if not sku and pn and pn != "未知":
            sku = sku_name_for_part_number(pn)
        if sku:
            label = sku
        by_part[label] = by_part.get(label, 0) + 1
        # R6-I8："按天"口径统一北京时间（与 admin overview 一致）
        day = (n.created_at + timedelta(hours=8)).strftime("%Y-%m-%d")
        by_day[day] = by_day.get(day, 0) + 1
    return {
        "days": days,
        "total_events": len(rows),
        "by_part": sorted(by_part.items(), key=lambda kv: kv[1], reverse=True),
        "by_day": sorted(by_day.items()),
    }
