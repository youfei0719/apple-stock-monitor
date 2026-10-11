"""产品/门店数据标准验证脚本。

运行: python -m app.api.routers.catalog_validate
"""
import sys

from app.api.routers.catalog import (
    SEED_PRODUCTS,
    SEED_STORES,
)


def validate_products():
    """A curated replenishment list need not cover every category or matrix axis."""
    errors = []
    seen = set()
    for product in SEED_PRODUCTS:
        part = product.get("part_number", "")
        if not part or part in seen:
            errors.append(f"商品编号缺失或重复: {part}")
        seen.add(part)
        for field in ["name", "category", "source_url", "price_cny"]:
            if not product.get(field):
                errors.append(f"{part}: 缺少字段 {field}")
        prelaunch = product.get("admission", {}).get("reason") == "prelaunch_watch"
        if prelaunch and not product.get("launch", {}).get("preorder_at"):
            errors.append(f"{part}: 缺少预约启用时间")
        if not prelaunch and not product.get("verification", {}).get("stores"):
            errors.append(f"{part}: 缺少库存核验")
        if not prelaunch and not product.get("admission", {}).get("shortage_stores"):
            errors.append(f"{part}: 缺少补货需求依据")
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
