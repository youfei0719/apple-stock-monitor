"""配额与会员：当前配额、四档说明（公开）。"""

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.db import get_db
from app.core.tiers import TIERS, tier_of
from app.models.models import MonitorTask, QuotaUsage, User

router = APIRouter(tags=["quota"])


@router.get("/quota")
def get_quota(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    period = datetime.utcnow().strftime("%Y-%m")
    usage = db.execute(
        select(QuotaUsage).where(QuotaUsage.user_id == user.id, QuotaUsage.period == period)
    ).scalar_one_or_none()
    tier = tier_of(user.tier)
    tasks_used = db.execute(
        select(func.count()).select_from(MonitorTask).where(MonitorTask.user_id == user.id)
    ).scalar()
    settings = get_settings()
    return {
        "tier": user.tier,
        "tier_expires_at": user.tier_expires_at.isoformat() + "Z" if user.tier_expires_at else None,
        "push_used": usage.push_count if usage else 0,
        "push_limit": tier["push_limit"],
        "tasks_used": tasks_used,
        "tasks_limit": tier["tasks_limit"],
        "refresh_interval_sec": settings.tier_intervals.get(user.tier, 300),
        "period": period,
    }


@router.get("/plans")
def list_plans():
    settings = get_settings()
    return [
        {
            "tier": tier,
            "name": info["name"],
            "price_cny": info["price_cny"],
            "tasks_limit": info["tasks_limit"],
            "push_limit": info["push_limit"],
            "channels": info["channels"],
            "history": info["history"],
            "priority": info["priority"],
            "refresh_interval_sec": settings.tier_intervals.get(tier),
        }
        for tier, info in TIERS.items()
    ]
