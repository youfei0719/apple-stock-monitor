#!/usr/bin/env python3
"""Validate official catalog evidence; do not impose an invented rectangular matrix."""

import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[1] / "backend/app/data/apple_catalog.json"


def validate_products(products):
    errors = []
    seen = set()
    for product in products:
        part = product.get("part_number", "")
        for key in ["part_number", "name", "category", "source_url", "product_url", "attributes"]:
            if key not in product or product[key] is None:
                errors.append(f"{part}: missing {key}")
        if not re.fullmatch(r"[A-Z0-9]{5}[A-Z]{2}/[AB]", part):
            errors.append(f"{part}: invalid retail part")
        if part in seen:
            errors.append(f"{part}: duplicate retail part")
        seen.add(part)
        price = product.get("price_cny")
        if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
            errors.append(f"{part}: invalid price")
        prelaunch = product.get("admission", {}).get("reason") == "prelaunch_watch"
        if not prelaunch and not product.get("admission", {}).get("shortage_stores"):
            errors.append(f"{part}: missing replenishment-demand evidence")
        if not product.get("monitorable") or (
            not prelaunch and not product.get("verification", {}).get("stores")
        ):
            errors.append(f"{part}: missing independent pickup verification")
        if prelaunch:
            launch = product.get("launch", {})
            try:
                opens_at = datetime.fromisoformat(launch.get("preorder_at", ""))
                if opens_at.utcoffset() is None:
                    errors.append(f"{part}: activation schedule needs an explicit timezone")
            except (ValueError, TypeError):
                errors.append(f"{part}: invalid activation schedule")
            if not launch.get("source_url", "").startswith("https://www.apple.com.cn/"):
                errors.append(f"{part}: missing official schedule source")
            if product.get("verification", {}).get("status") != "scheduled":
                errors.append(f"{part}: prelaunch status must not imply verified stock")
        if not product.get("source_url", "").startswith("https://www.apple.com.cn/"):
            errors.append(f"{part}: unofficial product source")
        if product.get("monitor_scope") not in ("product", "case"):
            errors.append(f"{part}: missing inventory scope")
        if product.get("category") == "watch" and product.get("monitor_scope") != "case":
            errors.append(f"{part}: unverified combined watch/band scope")
        if product.get("model_number") and not re.fullmatch(r"A\d{4}", product["model_number"]):
            errors.append(f"{part}: invalid hardware model number")
    return errors


def validate_stores(stores):
    seen = set()
    errors = []
    for store in stores:
        number = store.get("number")
        if not number or number in seen:
            errors.append(f"Duplicate or missing store number: {number}")
        seen.add(number)
        if not store.get("name") or not store.get("city"):
            errors.append(f"{number}: missing store identity")
    return errors


def main():
    data = json.loads(CATALOG.read_text())
    errors = validate_products(data["products"])
    if not data.get("verification_complete"):
        errors.append("Inventory verification is incomplete; do not publish as a full catalog")
    if not data.get("sources") or any(not s.get("sha256") for s in data["sources"]):
        errors.append("Missing official source fingerprints")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Validated {len(data['products'])} curated replenishment / scheduled retail SKUs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
