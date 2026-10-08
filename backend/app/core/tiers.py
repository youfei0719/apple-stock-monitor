"""会员四档定义：任务数 / 月推送配额 / 刷新间隔 / 渠道。"""

TIERS: dict[str, dict] = {
    "trial": {
        "name": "体验",
        "tasks_limit": 1,
        "push_limit": 1,
        "channels": ["page"],
        "history": False,
        "priority": False,
        "price_cny": 0,
    },
    "free": {
        "name": "免费",
        "tasks_limit": 3,
        "push_limit": 5,
        "channels": ["email"],
        "history": False,
        "priority": False,
        "price_cny": 0,
    },
    "standard": {
        "name": "标准",
        "tasks_limit": 10,
        "push_limit": 100,
        "channels": ["email"],
        "history": True,
        "priority": False,
        "price_cny": 19,
    },
    "pro": {
        "name": "Pro",
        "tasks_limit": 30,
        "push_limit": 500,
        # 注意：sms 短信通道尚未实现（send_sms 会 raise NotImplementedError），
        # 在短信供应商接入前不向任何档位开放，避免用户配了发不出去。
        "channels": ["email"],
        "history": True,
        "priority": True,
        "price_cny": 39,
    },
}

VALID_TIERS = tuple(TIERS.keys())


def tier_of(user_tier: str | None) -> dict:
    return TIERS.get(user_tier or "free", TIERS["free"])


def effective_tier(user) -> str:
    """有效档位：付费档过期后按 free 算。

    全库所有档位判断必须走这个函数，禁止直接读 user.tier（断裂-1）。
    user 可为 None（匿名 trial）→ 返回 "trial"。
    """
    if user is None:
        return "trial"
    tier = user.tier or "free"
    if tier in ("standard", "pro"):
        exp = getattr(user, "tier_expires_at", None)
        if exp is not None:
            from datetime import datetime, timezone

            now = exp.now(timezone.utc) if exp.tzinfo else datetime.utcnow()
            if now >= exp:
                return "free"
    return tier if tier in TIERS else "free"


def effective_tier_of(user) -> dict:
    return TIERS[effective_tier(user)]
