"""产品/门店数据标准验证脚本。

运行: python -m app.api.routers.catalog_validate
"""
import sys
from collections import defaultdict

from app.api.routers.catalog import (
    SEED_CITY_ANCHORS,
    SEED_PRODUCTS,
    SEED_STORES,
)


def validate_products():
    errors = []
    # 1. 字段完整
    required = ["part_number", "name", "color", "capacity", "price_cny", "category"]
    for i, p in enumerate(SEED_PRODUCTS):
        for f in required:
            if f not in p or p[f] is None or p[f] == "":
                errors.append(f"产品 #{i} 缺少字段 {f}: {p}")
    # 2. part_number 唯一
    seen = {}
    for p in SEED_PRODUCTS:
        pn = p.get("part_number")
        if pn in seen:
            errors.append(f"part_number 重复: {pn}")
        seen[pn] = p
    # 3. 价格一致：同一机型+容量，价格必须一致
    price_map = {}
    for p in SEED_PRODUCTS:
        key = (p.get("name"), p.get("capacity"))
        if key in price_map and price_map[key] != p.get("price_cny"):
            errors.append(f"价格不一致 {key}: {price_map[key]} vs {p.get('price_cny')}")
        price_map[key] = p.get("price_cny")
    # 4. 完整矩阵：每个机型的容量×颜色全组合
    by_model = defaultdict(list)
    for p in SEED_PRODUCTS:
        by_model[p.get("name")].append(p)
    for model, products in by_model.items():
        caps = set(p["capacity"] for p in products)
        colors = set(p["color"] for p in products)
        combos = set((p["capacity"], p["color"]) for p in products)
        expected = len(caps) * len(colors)
        if len(combos) != expected:
            missing = [
                (c, col) for c in caps for col in colors if (c, col) not in combos
            ]
            errors.append(
                f"{model} 矩阵不完整: 有 {len(combos)} 个，应有 {expected} 个，缺 {missing}"
            )
    # 5. 品类覆盖
    categories = set(p.get("category") for p in SEED_PRODUCTS)
    for cat in ["iphone", "ipad", "mac", "watch"]:
        if cat not in categories:
            errors.append(f"品类 {cat} 无产品（前端需标注'产品库建设中'）")
    return errors


def validate_stores():
    errors = []
    # 1. 字段完整
    for i, s in enumerate(SEED_STORES):
        for f in ["number", "name", "city", "province"]:
            if f not in s or not s[f]:
                errors.append(f"门店 #{i} 缺少字段 {f}: {s}")
    # 2. 编号唯一
    seen = set()
    for s in SEED_STORES:
        if s["number"] in seen:
            errors.append(f"门店编号重复: {s['number']}")
        seen.add(s["number"])
    # 3. 城市覆盖
    store_cities = set(s["city"] for s in SEED_STORES)
    for city in SEED_CITY_ANCHORS:
        if city not in store_cities:
            errors.append(f"锚点城市 {city} 无门店覆盖")
    return errors


if __name__ == "__main__":
    all_errors = []
    print("=== 产品目录 ===")
    errs = validate_products()
    all_errors.extend(errs)
    for e in errs:
        print(f"  ✕ {e}")
    if not errs:
        print("  ✓ 通过")
    print(f"=== 门店目录 ({len(SEED_STORES)} 家) ===")
    errs = validate_stores()
    all_errors.extend(errs)
    for e in errs:
        print(f"  ✕ {e}")
    if not errs:
        print("  ✓ 通过")
    print(f"\n共 {len(all_errors)} 个问题")
    sys.exit(1 if all_errors else 0)
