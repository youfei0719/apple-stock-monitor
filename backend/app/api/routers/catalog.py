"""目录（公开）：全国门店目录 + 在线刷新、产品目录。

门店目录以 system_config 的 store_catalog 为准；refresh=1 时按城市锚点
在线调用 pickup-message 重新发现，全局限流 1 次/小时。
"""

import time

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_optional_user
from app.api.errors import APIError
from app.core.db import SessionLocal, get_db
from app.core.logging import get_logger
from app.models.models import User
from app.services.apple_client import AppleClient, AppleError, AppleRateLimitError
from app.services.engine import get_config, set_config

router = APIRouter(prefix="/catalog", tags=["catalog"])
log = get_logger("catalog")

# 后台刷新互斥标记（进程内；防管理员连点导致并发刷新打爆 Apple 接口）
_refresh_running = False

STORE_CATALOG_KEY = "store_catalog"
PRODUCT_CATALOG_KEY = "products_catalog"
CITY_ANCHORS_KEY = "city_anchors"
REFRESH_AT_KEY = "store_catalog_refreshed_at"
REFRESH_COOLDOWN_SEC = 3600

# 种子门店（2026-10-09 实测可查的深圳三店 + 调研中的已知门店）
SEED_STORES = [
    {"number": "R761", "name": "Apple 万象城", "city": "深圳", "province": "广东"},
    {"number": "R484", "name": "Apple 益田假日广场", "city": "深圳", "province": "广东"},
    {"number": "R793", "name": "Apple 前海壹方城", "city": "深圳", "province": "广东"},
    {"number": "R359", "name": "Apple 南京东路", "city": "上海", "province": "上海"},
    {"number": "R389", "name": "Apple 浦东", "city": "上海", "province": "上海"},
    {"number": "R401", "name": "Apple 上海环贸 iapm", "city": "上海", "province": "上海"},
    {"number": "R678", "name": "Apple 静安", "city": "上海", "province": "上海"},
    {"number": "R683", "name": "Apple 环球港", "city": "上海", "province": "上海"},
    {"number": "R705", "name": "Apple 七宝", "city": "上海", "province": "上海"},
    {"number": "R581", "name": "Apple 五角场", "city": "上海", "province": "上海"},
    {"number": "R390", "name": "Apple 香港广场", "city": "上海", "province": "上海"},
    {"number": "R471", "name": "Apple 西湖", "city": "杭州", "province": "浙江"},
    {"number": "R532", "name": "Apple 杭州万象城", "city": "杭州", "province": "浙江"},
    {"number": "R531", "name": "Apple 天一广场", "city": "宁波", "province": "浙江"},
]

SEED_CITY_ANCHORS = [
    "深圳",
    "广州",
    "上海",
    "北京",
    "杭州",
    "南京",
    "苏州",
    "成都",
    "重庆",
    "武汉",
    "西安",
    "郑州",
    "长沙",
    "天津",
    "沈阳",
    "大连",
    "青岛",
    "济南",
    "厦门",
    "福州",
    "昆明",
    "南宁",
    "合肥",
    "无锡",
    "宁波",
    "温州",
]

# 种子产品（part number 为真实查询键；用户也可在专家模式直接输入任意 part number）
SEED_PRODUCTS = [
    {
        "part_number": "MJYC4CH/A",
        "name": "iPhone 18 Pro Max",
        "color": "银色",
        "capacity": "512GB",
        "price_cny": 12999,
        "category": "iphone",
    },
    {
        "part_number": "MJY64CH/A",
        "name": "iPhone 18 Pro Max",
        "color": "黑色",
        "capacity": "256GB",
        "price_cny": 9999,
        "category": "iphone",
    },
]


def _seed_if_empty(db: Session) -> None:
    if not get_config(db, STORE_CATALOG_KEY, {}).get("stores"):
        set_config(db, STORE_CATALOG_KEY, {"stores": SEED_STORES})
    if not get_config(db, CITY_ANCHORS_KEY, {}).get("anchors"):
        set_config(db, CITY_ANCHORS_KEY, {"anchors": SEED_CITY_ANCHORS})
    if not get_config(db, PRODUCT_CATALOG_KEY, {}).get("products"):
        set_config(db, PRODUCT_CATALOG_KEY, {"products": SEED_PRODUCTS})


def _do_refresh_stores() -> None:
    """后台刷新任务：独立 DB 会话，不占用请求 worker。"""
    global _refresh_running
    if _refresh_running:
        log.warning("catalog_refresh_already_running")
        return
    _refresh_running = True
    db = SessionLocal()
    try:
        client = AppleClient()
        anchors = get_config(db, CITY_ANCHORS_KEY, {}).get("anchors", SEED_CITY_ANCHORS)
        merged: dict[str, dict] = {}
        errors = 0
        for anchor in anchors:
            try:
                for s in client.discover_stores(anchor):
                    if s.get("number"):
                        merged[s["number"]] = {
                            "number": s["number"],
                            "name": s.get("name", ""),
                            "city": s.get("city", "") or anchor,
                            "province": "",
                        }
            except AppleRateLimitError as e:
                log.warning("catalog_refresh_rate_limited", anchor=anchor, error=str(e))
                # R6-P2-11：被 Apple 限流放弃时把占位时间清零——占位写的是
                # "已刷新"时间，若留着会烧掉 1 小时冷却（实际什么都没刷到）
                set_config(db, REFRESH_AT_KEY, {"at": 0})
                return  # 被 Apple 限流：本轮放弃，下次再刷
            except AppleError as e:
                errors += 1
                log.warning("catalog_refresh_anchor_failed", anchor=anchor, error=str(e))
        if merged:
            set_config(
                db,
                STORE_CATALOG_KEY,
                {"stores": sorted(merged.values(), key=lambda x: x["number"])},
            )
            set_config(db, REFRESH_AT_KEY, {"at": time.time()})
            log.info("catalog_refreshed", stores=len(merged), anchor_errors=errors)
        else:
            log.warning("catalog_refresh_empty", anchor_errors=errors)
    except Exception as e:
        log.error("catalog_refresh_failed", error=str(e))
    finally:
        db.close()
        _refresh_running = False


@router.get("/stores")
def list_stores(
    background_tasks: BackgroundTasks,
    refresh: int = Query(default=0, ge=0, le=1),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """R4-P0-5：refresh=1 成功时也返回纯数组（与 refresh=0 同形）。

    刷新是在线打 Apple 接口的重操作，走后台异步任务；本请求立即返回当前
    目录数组，前端轮询/稍后重拉即可拿到新数据。
    """
    _seed_if_empty(db)
    if refresh == 1:
        # 刷新是在线打 Apple 接口的重操作：仅管理员可触发，且走后台异步任务
        if not user or not user.is_admin:
            raise APIError(403, "刷新门店目录需要管理员权限", "forbidden")
        last = get_config(db, REFRESH_AT_KEY, {}).get("at", 0)
        if time.time() - last < REFRESH_COOLDOWN_SEC:
            raise APIError(429, "门店目录刷新限流：每小时 1 次", "refresh_limited")
        current = get_config(db, STORE_CATALOG_KEY, {}).get("stores", [])
        if _refresh_running:
            return current
        # 先占位写刷新时间，防并发重复触发
        set_config(db, REFRESH_AT_KEY, {"at": time.time()})
        background_tasks.add_task(_do_refresh_stores)
        log.info("catalog_refresh_enqueued", admin_id=user.id)
        return current
    stores = get_config(db, STORE_CATALOG_KEY, {}).get("stores", [])
    return stores


@router.get("/products")
def list_products(
    category: str = Query(default="iphone", pattern="^(iphone|ipad|mac|watch)$"),
    db: Session = Depends(get_db),
):
    _seed_if_empty(db)
    products = get_config(db, PRODUCT_CATALOG_KEY, {}).get("products", [])
    return [p for p in products if p.get("category") == category]


@router.get("/anchors")
def list_anchors(db: Session = Depends(get_db)):
    """城市锚点列表（调试/管理用）。"""
    _seed_if_empty(db)
    return get_config(db, CITY_ANCHORS_KEY, {}).get("anchors", [])
