#!/usr/bin/env python3
"""Build a curated replenishment catalog from official CN product data.

Run with backend/.venv/bin/python scripts/sync_apple_catalog.py --cache /tmp/apple-catalog
Selected launch families need shortage evidence or an official prelaunch schedule.
Never fabricate combinations or treat engraving, CTO or transient stock as product definitions.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx

BASE = "https://www.apple.com.cn"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "backend/app/data/apple_catalog.json"
# Admission shortlist: new launch families where replenishment demand is evidenced.
# Adding a new family is an editorial decision, never "import the entire store".
SOURCES = [
    ("iphone", "iPhone 18 Pro", "/shop/buy-iphone/iphone-18-pro"),
    ("iphone", "iPhone Duo", "/shop/buy-iphone/iphone-duo"),
]

# Hardware model disclosed in the official CN eSIM compatibility notice.
HARDWARE_MODELS = {"iPhone Duo": "A3721"}

PRELAUNCH = {
    "iPhone Duo": {
        "preorder_at": "2026-10-16T20:00:00+08:00",
        "release_date": "2026-10-23",
        "note": "机型将在获得批准后发售，具体以 Apple 实际开放购买为准",
        "source_url": BASE + "/shop/buy-iphone/iphone-duo",
    },
}
CATEGORY_LABELS = {
    "iphone": "iPhone",
    "ipad": "iPad",
    "mac": "Mac",
    "watch": "Apple Watch",
    "airpods": "AirPods",
    "homepod": "HomePod",
    "airtag": "AirTag",
    "display": "显示器",
    "vision": "Apple Vision Pro",
}
DIMENSIONS = {
    "dimensionCapacity": "存储容量",
    "dimensionColor": "颜色",
    "dimensionConnection": "网络",
    "dimensionFinish": "玻璃面板",
    "dimensionStandType": "支架",
    "dimensionPackQty": "包装数量",
    "dimensionScreensize": "屏幕尺寸",
    "storage-dimensionCapacity": "存储容量",
    "processor-cpuCoreCount-gpuCoreCount": "处理器",
    "dimensionCharging": "充电盒",
    "dimensionCase": "充电盒",
    "dimensionWireless": "充电盒",
    "processor-dimensionChip": "芯片",
    "processor-dimensionChip-cpuCoreCount-gpuCoreCount": "处理器",
    "chassis-dimensionColor": "颜色",
    "chassis-dimensionScreensize": "屏幕尺寸",
    "display-dimensionFinish": "玻璃面板",
    "watch_cases-dimensionCaseSize": "表壳尺寸",
    "watch_cases-dimensionCaseMaterial": "表壳材质",
    "watch_cases-dimensionColor": "颜色",
    "watch_cases-dimensionConnection": "网络",
    "apple_vision_pro_2025-dimensionCapacity": "存储容量",
}
DECODER = json.JSONDecoder()


def plain(value: str) -> str:
    value = re.sub(r"<as-footnote\b.*?</as-footnote>", "", str(value), flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", value))).strip()


def embedded(source: str, key: str) -> dict:
    match = re.search(rf'\b{re.escape(key)}["\']?\s*[:=]\s*', source)
    if not match:
        return {}
    try:
        result, _ = DECODER.raw_decode(source[match.end() :].lstrip())
    except ValueError:
        return {}
    return result if isinstance(result, dict) else {}


def metrics(source: str) -> dict:
    m = re.search(r'<script\b[^>]*id="metrics"[^>]*>(.*?)</script>', source, re.S)
    return json.loads(m[1]).get("data", {}) if m else {}


def label(display: dict, dimension: str, value: str) -> str:
    if dimension.endswith("dimensionScreensize") and re.fullmatch(r"\d+(_\d+)?inch", value):
        return value.removesuffix("inch").replace("_", ".") + " 英寸"
    info = display.get(dimension, {}).get(value, {})
    if isinstance(info, dict):
        text = info.get("text") or info.get("header") or info.get("value")
        if text:
            # Keep the selector name; explanatory HTML is not an option label.
            text = re.split(r'<(?:div|span)\b[^>]*class="(?:form-label-small|as-subheading)', text)[
                0
            ]
            return re.sub(r"^(新外观|新款)\s+", "", plain(text)).replace("\u200d", "")
    if re.fullmatch(r"\d+(gb|tb)", value):
        return value.upper()
    if re.fullmatch(r"\d+(_\d+)?inch", value):
        return value.removesuffix("inch").replace("_", ".") + " 英寸"
    if value in ("gps", "gpscell"):
        return "GPS" if value == "gps" else "GPS + 蜂窝网络"
    translations = {
        "aluminum": "铝金属",
        "titanium": "钛金属",
        "ceramic": "陶瓷",
        "keyboard_with_touch_id": "带触控 ID 的妙控键盘",
        "keyboard_without_touch_id": "妙控键盘",
        "chn": "中文 (拼音)",
        "che": "美式英语",
    }
    return translations.get(value, value)


def price_number(price: dict) -> float:
    current = price.get("currentPrice", {})
    for value in [current.get("raw_amount"), price.get("amount"), price.get("seoPrice")]:
        if value is not None:
            return float(value)
    raise ValueError("Official product is missing its price")


def attributes(product: dict, display: dict) -> dict[str, str]:
    dims = product.get("dimensions", product)
    result = {}
    for key, title in DIMENSIONS.items():
        if key in dims and dims[key] is not None:
            result[title] = label(display, key, str(dims[key]))
    # Capacity labels can include storage explainer text; the code is unambiguous.
    for key in ["dimensionCapacity", "apple_vision_pro_2025-dimensionCapacity"]:
        if dims.get(key):
            result["存储容量"] = str(dims[key]).upper()
    return result


def model_name(family: str, product: dict, attrs: dict) -> str:
    size = attrs.get("屏幕尺寸", "").split(" 英寸")[0]
    if family == "iPhone 18 Pro":
        return "iPhone 18 Pro Max" if product.get("familyType") == "iphone18promax" else family
    if family in ("iPad Air", "iPad Pro", "MacBook Air", "MacBook Pro") and size:
        name = f"{family} {size} 英寸"
        chip = attrs.get("芯片")
        if chip:
            name += f" ({chip})"
        return name
    return family


def record(
    category: str, family: str, product: dict, display: dict, source_url: str, price: float
) -> dict:
    attrs = attributes(product, display)
    name = model_name(family, product, attrs)
    part = product.get("partNumber") or product.get("btrOrFdPartNumber") or product.get("part")
    model = re.search(r"a\d{4}", str(product.get("energyComplianceKey", "")))
    return {
        "part_number": part,
        "sku": part.split("/")[0],
        "name": name,
        "family": family,
        "category": category,
        "color": attrs.get("颜色", ""),
        "capacity": attrs.get("存储容量", ""),
        "price_cny": price,
        "attributes": attrs,
        "model_number": model[0].upper() if model else HARDWARE_MODELS.get(family, ""),
        "source_url": source_url,
        "product_url": f"{BASE}/shop/product/{part}",
        "monitor_scope": "case" if category == "watch" else "product",
    }


class Importer:
    def __init__(self, cache: Path):
        self.cache = cache
        self.cache.mkdir(parents=True, exist_ok=True)
        self.sem = asyncio.Semaphore(3)
        self.source_evidence = []
        self.batch_results = []

    async def fetch(self, client: httpx.AsyncClient, path: str) -> tuple[str, str]:
        filename = path.strip("/").replace("/", "_") + ".html"
        target = self.cache / filename
        if target.exists() and datetime.now(UTC).timestamp() - target.stat().st_mtime < 86400:
            content = target.read_text()
            return content, BASE + path
        async with self.sem:
            response = await client.get(BASE + path)
            response.raise_for_status()
            content = response.text
            target.write_text(content)
            return content, str(response.url)

    async def mac_details(self, client, category, family, product, display, source_url):
        part = product["btrOrFdPartNumber"]
        content, url = await self.fetch(client, f"/shop/product/{part}")
        data = metrics(content)
        detail = next((p for p in data.get("products", []) if p.get("partNumber") == part), None)
        if detail is None:
            raise ValueError(f"Mac detail missing matching SKU: {part}")
        out = record(category, family, product, display, source_url, detail["price"]["fullPrice"])
        selection = data.get("buyflow", {}).get("userSelections", {})
        attrs = out["attributes"]
        # Mapping from Apple's resolved warm selection, never infer memory from price.
        for key, title in [
            ("MEMORY", "统一内存"),
            ("STORAGE", "存储容量"),
            ("KEYBOARD", "键盘"),
            ("KEYBOARD_LANGUAGE", "键盘语言"),
            ("ETHERNET_BANDWIDTH", "以太网"),
        ]:
            value = selection.get(key, {})
            if isinstance(value, dict) and value.get("dimensionValue"):
                dimension = {
                    "MEMORY": "memory-dimensionMemory",
                    "STORAGE": "storage-dimensionCapacity",
                    "KEYBOARD": "keyboard-dimensionKeyboard",
                    "KEYBOARD_LANGUAGE": "keyboard-localizationCode",
                    "ETHERNET_BANDWIDTH": "ethernet_adapter-ethernetBandwidth",
                }[key]
                config = embedded(content, "productSelectionData").get("configDisplayValues", {})
                attrs[title] = label(config, dimension, value["dimensionValue"]).split(" <")[0]
        cpu = selection.get("PROCESSOR", {})
        if cpu.get("cpuCoreCount") and cpu.get("gpuCoreCount"):
            attrs["处理器"] = f"{cpu['cpuCoreCount']} 核 CPU / {cpu['gpuCoreCount']} 核 GPU"
        chip = selection.get("PROCESSOR_CHIP")
        if chip and not attrs.get("芯片"):
            attrs["芯片"] = label(display, "processor-dimensionChip", chip)
        out["capacity"] = attrs.get("存储容量", "")
        out["configuration_codes"] = product.get("productConfiguration", {})
        out["product_url"] = url
        return out

    async def source(self, client, category, family, path):
        content, url = await self.fetch(client, path)
        selection = embedded(content, "productSelectionData")
        if not selection.get("products"):
            raise ValueError(f"Missing product selection: {path}")
        self.source_evidence.append(
            {
                "url": url,
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "variant_count": len(selection["products"]),
            }
        )
        display = selection.get("displayValues", selection.get("mainDisplayValues", {}))
        products = []
        details = []
        for product in selection["products"]:
            if product.get("comingSoon") or product.get("getReady") or product.get("isComingSoon"):
                continue
            if category == "mac":
                if product.get("type") == "CONFIGURABLE":
                    continue
                details.append(self.mac_details(client, category, family, product, display, url))
                continue
            price_key = product.get("fullPrice") or product.get("priceKey") or product.get("price")
            amount = price_number(display["prices"][price_key])
            out = record(category, family, product, display, url, amount)
            if category == "watch":
                bands = embedded(content, "bandSelectionData")
                out["related_band_styles"] = [
                    plain(x.get("sectionHeader", "")) for x in bands.get("items", {}).values()
                ]
                out["name"] += " · 表壳"
            products.append(out)
        products.extend(await asyncio.gather(*details))
        print(f"Fetched {family}: {len(products)} retail SKUs", flush=True)
        return products

    async def verify(self, client, products):
        # Read-only, bounded batches; no purchase, cart, cookie mutation or request fanout.
        excluded = []
        accepted = []
        scheduled = [p for p in products if p["family"] in PRELAUNCH]
        for product in scheduled:
            product["monitorable"] = True
            product["launch"] = PRELAUNCH[product["family"]]
            product["admission"] = {
                "reason": "prelaunch_watch",
                "selected_family": product["family"],
                "source_url": product["source_url"],
                "scope": "advance setup; availability must be confirmed after preorder opens",
            }
            product["verification"] = {"status": "scheduled", "source_url": product["source_url"]}
            accepted.append(product)
        products = [p for p in products if p not in scheduled]
        for start in range(0, len(products), 1):
            batch = products[start : start + 1]
            params = [("pl", "true")]
            params += [(f"parts.{i}", p["part_number"]) for i, p in enumerate(batch)]
            params.append(("store", "R359"))
            cache_key = hashlib.sha256(
                "|".join(p["part_number"] for p in batch).encode()
            ).hexdigest()[:20]
            cache_file = self.cache / f"pickup-{cache_key}.json"
            checkpoint = None
            if (
                cache_file.exists()
                and datetime.now(UTC).timestamp() - cache_file.stat().st_mtime < 86400
            ):
                checkpoint = json.loads(cache_file.read_text())
            if checkpoint is None:
                for attempt in range(3):
                    response = await client.get(
                        BASE + "/shop/retail/pickup-message",
                        params=params,
                        headers={"Referer": batch[0]["source_url"]},
                    )
                    if response.status_code not in (403, 541):
                        break
                    if attempt == 2:
                        raise RuntimeError(
                            "Apple rate limited this sync; keep the previous catalog "
                            "and resume after cooldown"
                        )
                    delay = max(120 * (attempt + 1), int(response.headers.get("Retry-After", "0")))
                    print(f"Apple rate limited: pausing {delay}s before bounded retry", flush=True)
                    await asyncio.sleep(delay)
                response.raise_for_status()
                checkpoint = {
                    "checked_at": datetime.now(UTC).isoformat(),
                    "parts": [p["part_number"] for p in batch],
                    "response": response.json(),
                }
                cache_file.write_text(json.dumps(checkpoint, ensure_ascii=False))
            data = checkpoint["response"]
            stores = data.get("body", {}).get("stores", [])
            if not stores:
                raise RuntimeError("Missing pickup response; keep the previous catalog")
            for product in batch:
                part = product["part_number"]
                evidence = [
                    dict(store=s.get("storeNumber"), **s.get("partsAvailability", {}).get(part, {}))
                    for s in stores
                    if part in s.get("partsAvailability", {})
                ]
                eligible = [
                    r
                    for r in evidence
                    if r.get("pickupDisplay") in ("available", "unavailable")
                    and r.get("storePickEligible") is True
                ]
                if not eligible:
                    excluded.append(
                        {
                            "part_number": part,
                            "name": product["name"],
                            "source_url": product["source_url"],
                            "reason": "pickup_not_supported",
                            "observed_displays": sorted(
                                {str(r.get("pickupDisplay")) for r in evidence}
                            ),
                        }
                    )
                    continue
                shortages = [r for r in eligible if r.get("pickupDisplay") == "unavailable"]
                if not shortages:
                    excluded.append(
                        {
                            "part_number": part,
                            "name": product["name"],
                            "source_url": product["source_url"],
                            "reason": "no_shortage_signal_in_sample",
                        }
                    )
                    continue
                product["admission"] = {
                    "reason": "launch_restock",
                    "selected_family": product["family"],
                    "shortage_stores": [r["store"] for r in shortages],
                    "checked_at": checkpoint["checked_at"],
                    "scope": "sampled stores only; not a nationwide or continuous-stock claim",
                }
                product["monitorable"] = True
                product["verification"] = {
                    "endpoint": BASE + "/shop/retail/pickup-message",
                    "checked_at": checkpoint["checked_at"],
                    "stores": sorted({r["store"] for r in eligible}),
                    "product_title": plain(
                        eligible[0]
                        .get("messageTypes", {})
                        .get("regular", {})
                        .get("storePickupProductTitle", "")
                    ),
                }
                accepted.append(product)
            if (start + 1) % 10 == 0 or start + 1 == len(products):
                print(f"Verified {start + 1}/{len(products)}", flush=True)
            await asyncio.sleep(2)
        return accepted, excluded

    async def run(self, output):
        async with httpx.AsyncClient(
            timeout=50,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/605.1.15 Version/18.1 Safari/605.1.15",
                "Accept": "application/json",
                "Referer": BASE + "/shop/",
            },
        ) as client:
            products = []
            # Family discovery is bounded and deterministic; each source must parse successfully.
            for category, family, path in SOURCES:
                products.extend(await self.source(client, category, family, path))
            unique = {p["part_number"]: p for p in products}
            products, excluded = await self.verify(client, list(unique.values()))
        now = datetime.now(UTC).isoformat()
        data = {
            "schema_version": 1,
            "version": now,
            "verification_complete": True,
            "region": "CN",
            "currency": "CNY",
            "scope": "Curated launch replenishment targets with observed store shortages; "
            "not a full Apple product catalog",
            "categories": [{"id": k, "label": v} for k, v in CATEGORY_LABELS.items()],
            "sources": self.source_evidence,
            "products": products,
            "excluded": excluded,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(output)
        print(f"Saved {len(products)} verified SKUs; excluded {len(excluded)}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    asyncio.run(Importer(args.cache).run(args.output))


if __name__ == "__main__":
    main()
