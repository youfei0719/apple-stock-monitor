"""时间工具：全库统一的 naive-UTC 时间归一化等。

R8-B0-3：原定义在 app.api.routers.tasks._as_naive_utc（R7 断裂 后-D-1），
admin 补单入口（patch_user）是同类漏洞的漏网之鱼——pydantic 把带时区的
ISO 字符串（如 "2026-12-01T08:00:00+08:00"）解析为 tz-aware datetime，
SQLite DATETIME 方言写入 tz-aware 时静默丢弃 tzinfo、不换算 UTC（实测
偏差 8 小时）。统一归一化入口提升到公共模块，各入口复用。
"""

from datetime import datetime, timezone


def as_naive_utc(dt: datetime | None) -> datetime | None:
    """pydantic 会把 "2026-12-01T00:00:00Z" / "...+08:00" 解析为 tz-aware
    datetime，aware 与 naive 直接比较（< now 等）会 TypeError→500，
    直接入库又会被 SQLite 丢掉 tzinfo 不换算 UTC。

    入库/比较前统一归一化为 naive UTC：aware 先转 UTC 再去 tzinfo，
    naive 保持原样。"""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt
