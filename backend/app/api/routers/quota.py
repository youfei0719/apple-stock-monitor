"""配额与会员：当前配额、四档说明（公开）、站点配置。"""

from fastapi import APIRouter, Depends, Header
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_optional_user
from app.api.errors import APIError
from app.core.config import get_settings
from app.core.db import get_db
from app.core.tiers import TIERS, effective_tier, effective_tier_of
from app.models.models import MonitorTask, QuotaUsage, User
from app.services.engine import ensure_quota_anchor, get_config, quota_period_key, trial_month_key

router = APIRouter(tags=["quota"])


@router.get("/quota")
def get_quota(
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None),
):
    settings = get_settings()
    if user is None:
        # F-4：匿名 trial 配额查询（Home trialExhausted 横幅用；体验 1 次/月真实生效）
        if not x_device_id:
            raise APIError(401, "需要登录或携带 X-Device-Id", "auth_required")
        info = TIERS["trial"]
        # R11-P1-5：匿名 trial 月 key 与引擎 _trial_quota_state 同口径（北京
        # 时间月份），此前此处用 UTC，每月 1 日 0:00–8:00 展示的 push_used
        # 与引擎扣减错月、trialExhausted 横幅误判。
        period = trial_month_key()
        used = int(get_config(db, f"trial_quota:{x_device_id}:{period}", {}).get("used", 0))
        tasks_used = db.execute(
            select(func.count())
            .select_from(MonitorTask)
            .where(MonitorTask.device_id == x_device_id, MonitorTask.user_id.is_(None))
        ).scalar()
        return {
            "tier": "trial",
            "tier_expires_at": None,
            "pending_tier": None,
            "quota_reset_at": None,
            "push_used": used,
            "push_limit": info["push_limit"],
            "tasks_used": tasks_used,
            "tasks_limit": info["tasks_limit"],
            "refresh_interval_sec": settings.tier_intervals.get("trial", 300),
            "period": period,
        }
    # 有效档位（过期按 free，断裂-1）；配额周期为购买日+30天滚动（不自洽-2）
    ensure_quota_anchor(db, user)
    period = quota_period_key(user)
    usage = db.execute(
        select(QuotaUsage).where(QuotaUsage.user_id == user.id, QuotaUsage.period == period)
    ).scalar_one_or_none()
    tier = effective_tier(user)
    info = effective_tier_of(user)
    tasks_used = db.execute(
        select(func.count()).select_from(MonitorTask).where(MonitorTask.user_id == user.id)
    ).scalar()
    return {
        "tier": tier,
        "tier_expires_at": user.tier_expires_at.isoformat() + "Z" if user.tier_expires_at else None,
        # R4-P1-D1：pending_tier 对用户可见（降级预约到期切换），到期前展示"到期后切换"
        "pending_tier": user.pending_tier,
        # 配额周期锚点（ISO 时间，前端按北京时间展示）；period 为锚点日期
        "quota_reset_at": user.quota_reset_at.isoformat() + "Z" if user.quota_reset_at else None,
        "push_used": usage.push_count if usage else 0,
        "push_limit": info["push_limit"],
        "tasks_used": tasks_used,
        "tasks_limit": info["tasks_limit"],
        "refresh_interval_sec": settings.tier_intervals.get(tier, 300),
        "period": period,
    }


@router.get("/plans")
def list_plans():
    # 不再返回 trial：trial 只用于未登录匿名体验，注册用户拿不到（不自洽-9）
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
        if tier != "trial"
    ]


@router.get("/site-config")
def site_config():
    """站点公开配置：爱发电赞助页 URL（断裂-14：前端付费指引跳转用）。"""
    settings = get_settings()
    return {"afdian_page_url": settings.AFDIAN_PAGE_URL}
