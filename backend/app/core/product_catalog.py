"""Versioned official CN catalog. Immutable snapshot; stock belongs to query results."""

import json
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "apple_catalog.json"
# Loaded once per process; an update is deployed/reloaded with the application.
CATALOG = json.loads(CATALOG_PATH.read_text())
PRODUCT_CATEGORIES = {c["id"]: c["label"] for c in CATALOG["categories"]}
OFFICIAL_PRODUCTS = CATALOG["products"]
CATALOG_VERSION = CATALOG["version"]


def product_configuration(product: dict) -> str:
    """Retain network, glass, chip, memory, case material and all actual SKU axes."""
    values = []
    for value in product.get("attributes", {}).values():
        if value and value not in values:
            values.append(value)
    if not values:
        values = [v for v in [product.get("capacity"), product.get("color")] if v]
    return " · ".join(values)


def product_title(product: dict) -> str:
    name = product.get("name", "")
    # Old hand-maintained catalogs keep their historical short product names.
    if not product.get("attributes"):
        return name
    details = product_configuration(product)
    return f"{name} · {details}" if details else name
