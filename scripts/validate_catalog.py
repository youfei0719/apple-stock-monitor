#!/usr/bin/env python3
"""产品/门店数据标准验证（不依赖 fastapi，可在 CI 跑）。

用法: python3 scripts/validate_catalog.py
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

CATALOG = Path(__file__).parent.parent / "backend/app/api/routers/catalog.py"


def parse():
    content = CATALOG.read_text()
    # 提取 SEED_PRODUCTS
    m = re.search(r"SEED_PRODUCTS = \[(.*?)\n\]", content, re.DOTALL)
    products = []
    for pm in re.finditer(
        r'"part_number": "([^"]*)",\s*"name": "([^"]+)",\s*"color": "([^"]+)",\s*'
        r'"capacity": "([^"]+)",\s*"price_cny": (\d+),\s*"category": "([^"]+)"',
        m.group(1),
    ):
        products.append({
            "part_number": pm.group(1),
            "name": pm.group(2),
            "color": pm.group(3),
            "capacity": pm.group(4),
            "price_cny": int(pm.group(5)),
            "category": pm.group(6),
        })
    # 提取 SEED_STORES
    m = re.search(r"SEED_STORES = \[(.*?)\n\]", content, re.DOTALL)
    stores = []
    for sm in re.finditer(
        r'"number": "([^"]+)",\s*"name": "([^"]+)",\s*"city": "([^"]+)",\s*"province": "([^"]+)"',
        m.group(1),
    ):
        stores.append({
            "number": sm.group(1),
            "name": sm.group(2),
            "city": sm.group(3),
            "province": sm.group(4),
        })
    return products, stores


def validate_products(products):
    errors = []
    # 1. 字段完整（part_number 允许为空，表示未验证需手动输入）
    for p in products:
        for f in ["name", "color", "capacity", "price_cny", "category"]:
            if not p.get(f):
                errors.append(f"产品缺少字段 {f}: {p}")
        if "part_number" not in p:
            errors.append(f"产品缺少 part_number 键: {p}")
    # 2. part_number 唯一（空字符串除外，空表示未验证需手动输入）
    seen = set()
    for p in products:
        pn = p["part_number"]
        if not pn:
            continue
        if pn in seen:
            errors.append(f"part_number 重复: {pn}")
        seen.add(pn)
    # 2b. 统计未验证的
    unverified = [p for p in products if not p["part_number"]]
    if unverified:
        print(f"  ! {len(unverified)} 个 SKU 无 part_number（需用户手动输入）")
    # 3. 价格一致
    price_map = {}
    for p in products:
        key = (p["name"], p["capacity"])
        if key in price_map and price_map[key] != p["price_cny"]:
            errors.append(f"价格不一致 {key}: {price_map[key]} vs {p['price_cny']}")
        price_map[key] = p["price_cny"]
    # 4. 完整矩阵
    by_model = defaultdict(list)
    for p in products:
        by_model[p["name"]].append(p)
    for model, items in by_model.items():
        caps = set(x["capacity"] for x in items)
        colors = set(x["color"] for x in items)
        combos = set((x["capacity"], x["color"]) for x in items)
        expected = len(caps) * len(colors)
        if len(combos) != expected:
            missing = [(c, col) for c in caps for col in colors if (c, col) not in combos]
            errors.append(f"{model} 矩阵不完整: 缺 {missing}")
    # 5. 品类覆盖（ipad/mac/watch 允许缺失，但前端必须标注）
    cats = set(p["category"] for p in products)
    for cat in ["ipad", "mac", "watch"]:
        if cat not in cats:
            print(f"  ! 品类 {cat} 无产品（前端需标注'产品库建设中'）")
    return errors


def validate_stores(stores):
    errors = []
    for s in stores:
        for f in ["number", "name", "city", "province"]:
            if not s.get(f):
                errors.append(f"门店缺少字段 {f}: {s}")
    seen = set()
    for s in stores:
        if s["number"] in seen:
            errors.append(f"门店编号重复: {s['number']}")
        seen.add(s["number"])
    return errors


if __name__ == "__main__":
    products, stores = parse()
    print(f"产品: {len(products)} 个, 门店: {len(stores)} 家")
    errs = validate_products(products) + validate_stores(stores)
    for e in errs:
        print(f"  ✕ {e}")
    if errs:
        print(f"\n{len(errs)} 个问题")
        sys.exit(1)
    print("  ✓ 全部通过")
