"""Scheduled product activation. The schedule starts checks; it never proves stock."""

from datetime import datetime

from app.core.product_catalog import OFFICIAL_PRODUCTS
from app.core.timeutil import as_naive_utc, utcnow

LAUNCH_PRODUCTS = {
    p["part_number"]: p for p in OFFICIAL_PRODUCTS if p.get("launch", {}).get("preorder_at")
}


def launch_for_part(part_number: str) -> dict | None:
    product = LAUNCH_PRODUCTS.get(part_number.strip().upper())
    return product.get("launch") if product else None


def waiting_for_launch(part_number: str, now: datetime | None = None) -> bool:
    launch = launch_for_part(part_number)
    if launch is None:
        return False
    opens_at = as_naive_utc(datetime.fromisoformat(launch["preorder_at"]))
    return (as_naive_utc(now) if now is not None else utcnow()) < opens_at
