"""历史与数据：有货事件（活动日志）、放货记录。"""

import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.routers.catalog import sku_name_for_part_number
from app.core.db import get_db
from app.core.tiers import effective_tier_of
from app.core.timeutil import utcnow
from app.models.models import MonitorTask, Notification, User

router = APIRouter(prefix="/history", tags=["history"])


def _since(days: int) -> datetime:
    return utcnow() - timedelta(days=days)


@router.get("/events")
def events(
    part_number: str | None = Query(default=None),
    store: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    # R4-P2：加 limit（days≤365 全量进内存）。过滤下推到 SQL 再限行，
    # 避免先截断再过滤导致漏数据。
    limit: int = Query(default=500, ge=1, le=2000),
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
    if part_number:
        # R5-B-N2：用 part_number 快照列过滤——任务删除后 task_id 置 NULL、
        # join 不到 MonitorTask，只有快照能命中
        q = q.where(
            or_(
                Notification.part_number == part_number,
                MonitorTask.part_number == part_number,
            )
        )
    if store:
        # R6-P2-20：转义 LIKE 通配符 % / _（及转义符自身），防用户输入改写匹配语义
        escaped = (
            store.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        q = q.where(Notification.body.like(f"%{escaped}%", escape="\\"))
    q = q.limit(limit)
    out = []
    for n, t in db.execute(q).all():
        # R5-B-N2：part_number 取通知创建时的快照列，任务删除后仍有型号信息
        pn = n.part_number or (t.part_number if t else "") or ""
        # P1：历史老文案里裸写了 part_number（如 MJYC4CH/A），展示时替换为完整 SKU 名。
        # 优先用任务的产品信息，任务不存在或无产品信息时用目录映射兜底。
        title, body = n.title, n.body
        sku = ""
        if t and (t.product_name or t.capacity or t.color):
            sku = (t.product_name or "").strip()
            if t.capacity:
                sku += f" {t.capacity}"
            if t.color:
                sku += f" {t.color}"
            sku = sku.strip()
        if not sku and pn:
            sku = sku_name_for_part_number(pn)
        if sku and pn:
            title = (title or "").replace(pn, sku)
            body = (body or "").replace(pn, sku)
        # P1：老文案门店名查找失败时会写成 "R793（R793）"，展示时合并重复
        if body:
            body = re.sub(r"(\S+?)（\1）", r"\1", body)
        out.append(
            {
                "id": n.id,
                "task_id": n.task_id,
                "part_number": pn,
                "title": title,
                "body": body,
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
    # R5-B-N2：按快照列聚合——任务删除后 join 不到 MonitorTask，
    # coalesce 保证已删任务的通知仍按原型号归组
    pn_col = func.coalesce(Notification.part_number, MonitorTask.part_number)
    # P1：同时返回产品名，前端不再裸显 part_number
    name_col = func.coalesce(MonitorTask.product_name, Notification.part_number)
    # R6-I8："按天"口径统一北京时间（与 admin overview 一致）
    day_col = func.date(Notification.created_at, "+8 hours")
    rows = db.execute(
        select(
            day_col.label("day"),
            pn_col.label("part_number"),
            name_col.label("product_name"),
            func.count().label("events"),
        )
        .join(MonitorTask, Notification.task_id == MonitorTask.id, isouter=True)
        .where(Notification.user_id == user.id)
        .where(Notification.kind == "stock_alert")
        .where(Notification.status == "sent")
        .where(Notification.created_at >= _since(days))
        .group_by(day_col, pn_col, name_col)
        .order_by(desc("day"))
    ).all()
    out = []
    for r in rows:
        # P1：product_name 为空或就是 part_number 时，用目录映射兜底
        pname = r.product_name or ""
        if (not pname or pname == r.part_number) and r.part_number:
            mapped = sku_name_for_part_number(r.part_number)
            if mapped:
                pname = mapped
        out.append(
            {
                "day": str(r.day),
                "part_number": r.part_number,
                "product_name": pname,
                "events": r.events,
            }
        )
    return out
