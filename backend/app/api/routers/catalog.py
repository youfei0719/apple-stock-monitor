"""目录（公开）：全国门店目录、产品目录。

门店目录以 system_config 的 store_catalog 为准；在线刷新是重操作，已拆到
POST /api/admin/catalog/refresh（管理后台，走 TOTP 二次验证），公开接口
不再接受 refresh=1（R13-P3-10）。
"""

import time

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

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
    {"number": "R401", "name": "Apple 环贸 iapm", "city": "上海", "province": "上海"},
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
# P1：完整 SKU 矩阵——每个机型的容量×颜色全组合，不留"选了没货"的坑
SEED_PRODUCTS = [
    # iPhone 18 Pro Max：256GB/512GB/1TB × 银色/黑色 = 6
    {
        "part_number": "MJY64CH/A",
        "name": "iPhone 18 Pro Max",
        "color": "黑色",
        "capacity": "256GB",
        "price_cny": 9999,
        "category": "iphone",
    },
    {
        "part_number": "MJY84CH/A",
        "name": "iPhone 18 Pro Max",
        "color": "银色",
        "capacity": "256GB",
        "price_cny": 9999,
        "category": "iphone",
    },
    {
        "part_number": "MJY74CH/A",
        "name": "iPhone 18 Pro Max",
        "color": "黑色",
        "capacity": "512GB",
        "price_cny": 12999,
        "category": "iphone",
    },
    {
        "part_number": "MJYC4CH/A",
        "name": "iPhone 18 Pro Max",
        "color": "银色",
        "capacity": "512GB",
        "price_cny": 12999,
        "category": "iphone",
    },
    {
        "part_number": "MJY94CH/A",
        "name": "iPhone 18 Pro Max",
        "color": "黑色",
        "capacity": "1TB",
        "price_cny": 14999,
        "category": "iphone",
    },
    {
        "part_number": "MJYD4CH/A",
        "name": "iPhone 18 Pro Max",
        "color": "银色",
        "capacity": "1TB",
        "price_cny": 14999,
        "category": "iphone",
    },
    # iPhone 18 Pro：256GB/512GB/1TB × 黑色钛金属/白色钛金属/原色钛金属 = 9
    {
        "part_number": "MJX64CH/A",
        "name": "iPhone 18 Pro",
        "color": "黑色钛金属",
        "capacity": "256GB",
        "price_cny": 8999,
        "category": "iphone",
    },
    {
        "part_number": "MJX84CH/A",
        "name": "iPhone 18 Pro",
        "color": "白色钛金属",
        "capacity": "256GB",
        "price_cny": 8999,
        "category": "iphone",
    },
    {
        "part_number": "MJX94CH/A",
        "name": "iPhone 18 Pro",
        "color": "原色钛金属",
        "capacity": "256GB",
        "price_cny": 8999,
        "category": "iphone",
    },
    {
        "part_number": "MJXA4CH/A",
        "name": "iPhone 18 Pro",
        "color": "黑色钛金属",
        "capacity": "512GB",
        "price_cny": 10999,
        "category": "iphone",
    },
    {
        "part_number": "MJXC4CH/A",
        "name": "iPhone 18 Pro",
        "color": "白色钛金属",
        "capacity": "512GB",
        "price_cny": 10999,
        "category": "iphone",
    },
    {
        "part_number": "MJXE4CH/A",
        "name": "iPhone 18 Pro",
        "color": "原色钛金属",
        "capacity": "512GB",
        "price_cny": 10999,
        "category": "iphone",
    },
    {
        "part_number": "MJXB4CH/A",
        "name": "iPhone 18 Pro",
        "color": "黑色钛金属",
        "capacity": "1TB",
        "price_cny": 12999,
        "category": "iphone",
    },
    {
        "part_number": "MJXD4CH/A",
        "name": "iPhone 18 Pro",
        "color": "原色钛金属",
        "capacity": "1TB",
        "price_cny": 12999,
        "category": "iphone",
    },
    {
        "part_number": "MJXF4CH/A",
        "name": "iPhone 18 Pro",
        "color": "白色钛金属",
        "capacity": "1TB",
        "price_cny": 12999,
        "category": "iphone",
    },
    # iPhone 18：128GB/256GB/512GB × 黑色/白色/群青色/粉色 = 12
    {
        "part_number": "MJW24CH/A",
        "name": "iPhone 18",
        "color": "黑色",
        "capacity": "128GB",
        "price_cny": 5999,
        "category": "iphone",
    },
    {
        "part_number": "MJW34CH/A",
        "name": "iPhone 18",
        "color": "白色",
        "capacity": "128GB",
        "price_cny": 5999,
        "category": "iphone",
    },
    {
        "part_number": "MJW44CH/A",
        "name": "iPhone 18",
        "color": "群青色",
        "capacity": "128GB",
        "price_cny": 5999,
        "category": "iphone",
    },
    {
        "part_number": "MJW54CH/A",
        "name": "iPhone 18",
        "color": "粉色",
        "capacity": "128GB",
        "price_cny": 5999,
        "category": "iphone",
    },
    {
        "part_number": "MJW64CH/A",
        "name": "iPhone 18",
        "color": "黑色",
        "capacity": "256GB",
        "price_cny": 6999,
        "category": "iphone",
    },
    {
        "part_number": "MJW74CH/A",
        "name": "iPhone 18",
        "color": "白色",
        "capacity": "256GB",
        "price_cny": 6999,
        "category": "iphone",
    },
    {
        "part_number": "MJW84CH/A",
        "name": "iPhone 18",
        "color": "群青色",
        "capacity": "256GB",
        "price_cny": 6999,
        "category": "iphone",
    },
    {
        "part_number": "MJW94CH/A",
        "name": "iPhone 18",
        "color": "粉色",
        "capacity": "256GB",
        "price_cny": 6999,
        "category": "iphone",
    },
    {
        "part_number": "MJWA4CH/A",
        "name": "iPhone 18",
        "color": "黑色",
        "capacity": "512GB",
        "price_cny": 8999,
        "category": "iphone",
    },
    {
        "part_number": "MJWB4CH/A",
        "name": "iPhone 18",
        "color": "白色",
        "capacity": "512GB",
        "price_cny": 8999,
        "category": "iphone",
    },
    {
        "part_number": "MJWC4CH/A",
        "name": "iPhone 18",
        "color": "群青色",
        "capacity": "512GB",
        "price_cny": 8999,
        "category": "iphone",
    },
    {
        "part_number": "MJWD4CH/A",
        "name": "iPhone 18",
        "color": "粉色",
        "capacity": "512GB",
        "price_cny": 8999,
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
        # R9-D1：以现有目录为底再合并——任一 anchor 抛 AppleError 时，不再删掉
        # 失败 anchor 的门店（含种子店）；只覆盖成功刷到的部分。
        merged: dict[str, dict] = {}
        for s in get_config(db, STORE_CATALOG_KEY, {}).get("stores", []):
            if s.get("number"):
                merged[s["number"]] = s
        errors = 0
        refreshed_any = False
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
                refreshed_any = True
            except AppleRateLimitError as e:
                log.warning("catalog_refresh_rate_limited", anchor=anchor, error=str(e))
                # R6-P2-11：被 Apple 限流放弃时把占位时间清零——占位写的是
                # "已刷新"时间，若留着会烧掉 1 小时冷却（实际什么都没刷到）
                set_config(db, REFRESH_AT_KEY, {"at": 0})
                return  # 被 Apple 限流：本轮放弃，下次再刷
            except AppleError as e:
                errors += 1
                log.warning("catalog_refresh_anchor_failed", anchor=anchor, error=str(e))
        if refreshed_any and merged:
            set_config(
                db,
                STORE_CATALOG_KEY,
                {"stores": sorted(merged.values(), key=lambda x: x["number"])},
            )
            set_config(db, REFRESH_AT_KEY, {"at": time.time()})
            log.info("catalog_refreshed", stores=len(merged), anchor_errors=errors)
        elif merged:
            # R15-P2-2：全部 anchor 失败（普通 AppleError）同样清零占位刷新时间，
            # 与 AppleRateLimitError 分支同口径——否则管理员被锁 1 小时冷却
            # （enqueue 时已先占位写入"已刷新"时间）。保留旧目录、不覆盖。
            set_config(db, REFRESH_AT_KEY, {"at": 0})
            log.warning(
                "catalog_refresh_all_failed_kept_existing",
                anchor_errors=errors,
                kept=len(merged),
            )
        else:
            # 目录本就为空且全部失败：同样清零占位，不烧 1 小时冷却
            set_config(db, REFRESH_AT_KEY, {"at": 0})
            log.warning("catalog_refresh_empty", anchor_errors=errors)
    except Exception as e:
        log.error("catalog_refresh_failed", error=str(e))
        # R17-P2-1：非 AppleError 异常（接口形状变更、DB 异常）同样清零占位
        # 刷新时间，否则管理员被锁 1 小时冷却；先 rollback 防会话已坏。
        try:
            db.rollback()
            set_config(db, REFRESH_AT_KEY, {"at": 0})
        except Exception as e2:
            log.error("catalog_refresh_cooldown_clear_failed", error=str(e2))
    finally:
        db.close()
        _refresh_running = False


def enqueue_catalog_refresh(
    db: Session, background_tasks: BackgroundTasks, admin_user: User
) -> list:
    """R13-P3-10：门店目录在线刷新（重操作）——按城市锚点在线调用
    pickup-message 重新发现，全局限流 1 次/小时。

    仅供管理后台调用（POST /api/admin/catalog/refresh，经 get_current_admin
    的 TOTP 二次验证）；公开接口不再直接触发。
    """
    last = get_config(db, REFRESH_AT_KEY, {}).get("at", 0)
    if time.time() - last < REFRESH_COOLDOWN_SEC:
        raise APIError(429, "门店目录刷新限流：每小时 1 次", "refresh_limited")
    current = get_config(db, STORE_CATALOG_KEY, {}).get("stores", [])
    if _refresh_running:
        return current
    # 先占位写刷新时间，防并发重复触发
    set_config(db, REFRESH_AT_KEY, {"at": time.time()})
    background_tasks.add_task(_do_refresh_stores)
    log.info("catalog_refresh_enqueued", admin_id=admin_user.id)
    return current


@router.get("/stores")
def list_stores(
    background_tasks: BackgroundTasks,
    refresh: int = Query(default=0, ge=0, le=1),
    db: Session = Depends(get_db),
):
    """R13-P3-10：公开接口不再接受 refresh=1——门店目录在线刷新是重操作，
    唯一入口为管理后台 POST /api/admin/catalog/refresh（经 TOTP 二次验证，
    全局限流 1 次/小时）。公开接口传 refresh=1 统一 403。

    本接口只读当前目录并返回纯数组。旧 R4-P0-5 docstring 已作废。
    """
    _seed_if_empty(db)
    if refresh == 1:
        # R13-P3-10：公开接口不再接受 refresh=1——原先仅凭 user.is_admin
        # 判断，绕过了管理后台的 TOTP 二次验证；刷新请走管理后台
        # POST /api/admin/catalog/refresh
        raise APIError(403, "门店目录刷新请使用管理后台", "use_admin_refresh")
    stores = get_config(db, STORE_CATALOG_KEY, {}).get("stores", [])
    return stores


@router.get("/products")
def list_products(
    category: str = Query(default="iphone", pattern="^(iphone|ipad|mac|watch)$"),
    db: Session = Depends(get_db),
):
    _seed_if_empty(db)
    products = get_config(db, PRODUCT_CATALOG_KEY, {}).get("products", [])
    # UX：按 part_number 去重并保持首次出现顺序——目录内容相同时渲染永远一致，
    # 不因数据源顺序抖动让用户觉得"刚才看到的那款去哪了"
    seen: dict[str, dict] = {}
    for p in products:
        if p.get("category") != category:
            continue
        pn = str(p.get("part_number") or "")
        if pn and pn not in seen:
            seen[pn] = p
    return list(seen.values())


@router.get("/anchors")
def list_anchors(db: Session = Depends(get_db)):
    """城市锚点列表（调试/管理用）。"""
    _seed_if_empty(db)
    return get_config(db, CITY_ANCHORS_KEY, {}).get("anchors", [])
