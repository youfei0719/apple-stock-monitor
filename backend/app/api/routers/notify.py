"""通知：链路测试、通知历史、通道健康。"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.errors import APIError
from app.core.db import get_db
from app.core.tiers import effective_tier_of
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


def _mask_target(channel: str, target: str) -> str:
    """R4-P2：webhook URL / bark key 只返回掩码，不返回明文。

    email 是用户自己的邮箱，保留明文供展示；其他通道的 target 可能是
    敏感密钥/URL，统一掩码。
    R6-P2-19：page 通道 target 为 device_id，同样掩码，不明文返回。
    """
    if channel in ("bark", "wecom", "dingtalk", "feishu", "page") and target:
        return (target[:6] + "***") if len(target) > 6 else "***"
    return target


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
    # N14-B：测试走档位校验。当前档位不支持的通道，明确提示不支持而不是显示成功。
    # 判定在每日限额之前：被拒绝的测试不消耗测试次数。
    tier_info = effective_tier_of(user)
    if data.channel not in tier_info["channels"]:
        _names = {"page": "站内", "email": "邮件", "bark": "Bark", "sms": "短信"}
        allowed_names = "、".join(_names.get(c, c) for c in tier_info["channels"])
        raise APIError(
            400,
            f"当前档位（{tier_info['name']}）仅支持{allowed_names}通知；"
            "Bark / 群机器人可配置，但到货不会发送（测试通过≠到货会发）",
            "channel_not_supported",
        )
    # R6-P2-9：email 测试目标必须是用户本人邮箱（管理员除外），防拿测试
    # 接口往任意邮箱发垃圾邮件
    if data.channel == "email" and not user.is_admin:
        if data.target.strip().lower() != (user.email or "").strip().lower():
            raise APIError(400, "测试邮箱必须是你账号绑定的邮箱", "bad_test_target")
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

    # R4-P2：不再把 30 天全量行拉进 Python。7d 成功率走 SQL 聚合，
    # 30d 活跃通道走 distinct（截断 50 个），last_failure 按通道各查 1 行。
    agg = db.execute(
        select(Notification.channel, Notification.status, func.count())
        .where(
            Notification.user_id == user.id,
            Notification.created_at >= since_7d,
            Notification.status.in_(["sent", "failed"]),
        )
        .group_by(Notification.channel, Notification.status)
    ).all()
    counts_7d = {(ch, st): c for ch, st, c in agg}
    recent_channels = set(
        db.execute(
            select(func.distinct(Notification.channel))
            .where(
                Notification.user_id == user.id,
                Notification.created_at >= since_30d,
            )
            .limit(50)
        )
        .scalars()
        .all()
    )

    channels = []
    for key, name in CHANNEL_META:
        sent = counts_7d.get((key, "sent"), 0)
        failed = counts_7d.get((key, "failed"), 0)
        last_fail = None
        if failed:
            last_fail = db.execute(
                select(Notification)
                .where(
                    Notification.user_id == user.id,
                    Notification.channel == key,
                    Notification.status == "failed",
                )
                .order_by(desc(Notification.created_at))
                .limit(1)
            ).scalar_one_or_none()
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
            # R4-P2：webhook URL / bark key 脱敏，只返回掩码
            "target": _mask_target(n.channel, n.target or ""),
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
