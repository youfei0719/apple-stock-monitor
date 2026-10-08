"""目录（公开）：全国门店目录 + 在线刷新、产品目录。

门店目录以 system_config 的 store_catalog 为准；refresh=1 时按城市锚点
在线调用 pickup-message 重新发现，全局限流 1 次/小时。
"""

import time

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.errors import APIError
from app.core.db import get_db
from app.core.logging import get_logger
from app.services.apple_client import AppleClient, AppleError, AppleRateLimitError
from app.services.engine import get_config, set_config

router = APIRouter(prefix="/catalog", tags=["catalog"])
log = get_logger("catalog")

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


@router.get("/stores")
def list_stores(
    refresh: int = Query(default=0, ge=0, le=1),
    db: Session = Depends(get_db),
):
    _seed_if_empty(db)
    if refresh == 1:
        last = get_config(db, REFRESH_AT_KEY, {}).get("at", 0)
        if time.time() - last < REFRESH_COOLDOWN_SEC:
            raise APIError(429, "门店目录刷新限流：每小时 1 次", "refresh_limited")
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
                raise APIError(429, "Apple 接口限流，稍后再试", "apple_rate_limited") from e
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


def seed_catalog(db: Session) -> None:
    """供 alembic/启动时调用的种子写入（幂等）。"""
    _seed_if_empty(db)
