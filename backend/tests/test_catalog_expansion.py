"""Evidence, selectors and persisted configuration regressions."""

import importlib.util
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.routers import catalog
from app.core.db import Base
from app.core.product_catalog import CATALOG, OFFICIAL_PRODUCTS, product_title
from app.services.engine import set_config

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "sync_catalog", ROOT / "scripts/sync_apple_catalog.py"
)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def test_official_rows_have_unique_query_evidence():
    parts = [p["part_number"] for p in OFFICIAL_PRODUCTS]
    assert len(set(parts)) == len(parts)
    for p in OFFICIAL_PRODUCTS:
        assert p["monitorable"]
        if p["admission"]["reason"] == "prelaunch_watch":
            assert p["launch"]["preorder_at"]
            assert p["verification"]["status"] == "scheduled"
            assert not p["verification"].get("stores")
        else:
            assert p["admission"]["shortage_stores"]
            assert p["verification"]["stores"]
        assert p["source_url"].startswith("https://www.apple.com.cn/")
        assert p["price_cny"] > 0


def test_legacy_database_cannot_hide_new_snapshot(db):
    set_config(
        db,
        catalog.PRODUCT_CATALOG_KEY,
        {
            "products": [
                {"part_number": "MK2M4CH/A", "name": "iPhone Duo", "category": "iphone"},
                {"part_number": "", "name": "iPhone 18 Pro Max", "category": "iphone"},
            ]
        },
    )
    assert catalog.current_products(db) == OFFICIAL_PRODUCTS


def test_custom_catalog_entry_is_preserved(db):
    custom = {"part_number": "CUSTOMCH/A", "name": "自定义", "category": "iphone"}
    set_config(db, catalog.PRODUCT_CATALOG_KEY, {"products": [custom]})
    assert custom in catalog.current_products(db)


def test_prelaunch_family_has_official_configurations_and_schedule():
    products = [p for p in OFFICIAL_PRODUCTS if p["family"] == "iPhone Duo"]
    assert len(products) == 8
    assert {p["color"] for p in products} == {"夜空色", "星光白色"}
    assert {p["capacity"] for p in products} == {"256GB", "512GB", "1TB", "2TB"}
    assert all(p["model_number"] == "A3721" for p in products)
    assert all(p["admission"]["reason"] == "prelaunch_watch" for p in products)


def test_product_title_preserves_network_and_glass():
    p = {
        "name": "iPad Pro",
        "capacity": "1TB",
        "color": "银色",
        "attributes": {
            "存储容量": "1TB",
            "颜色": "银色",
            "网络": "蜂窝网络",
            "玻璃面板": "纳米纹理",
        },
    }
    assert "蜂窝网络" in product_title(p)
    assert "纳米纹理" in product_title(p)


def test_parser_reads_json_without_executing_scripts():
    source = 'window.BOOT = { productSelectionData: {"products":[{"partNumber":"AA123CH/A"}]} };'
    assert sync.embedded(source, "productSelectionData")["products"][0]["partNumber"] == "AA123CH/A"
    assert sync.embedded("productSelectionData: maliciousFunction()", "productSelectionData") == {}


def test_hardware_screen_size_does_not_become_product_name():
    assert (
        sync.label(
            {"dimensionScreensize": {"6_9inch": {"value": "iPhone Pro Max"}}},
            "dimensionScreensize",
            "6_9inch",
        )
        == "6.9 英寸"
    )


def test_mac_color_label_is_not_truncated():
    assert (
        sync.label(
            {"dimensionColor": {"spaceblack": {"value": "深空黑色"}}},
            "dimensionColor",
            "spaceblack",
        )
        == "深空黑色"
    )


def test_empty_attributes_are_valid_for_fixed_product():
    p = {"name": "AirPods Pro", "attributes": {}, "part_number": "AA123CH/A"}
    assert product_title(p) == "AirPods Pro"


def test_categories_count_only_verified_snapshot():
    result = catalog.list_categories()
    assert sum(c["sku_count"] for c in result["categories"]) == len(OFFICIAL_PRODUCTS)
    assert result["version"] == CATALOG["version"]


def test_default_catalog_is_a_shortlist_not_the_entire_store(db):
    assert {p["category"] for p in OFFICIAL_PRODUCTS} == {"iphone"}
    assert {p["name"] for p in OFFICIAL_PRODUCTS} == {
        "iPhone 18 Pro",
        "iPhone 18 Pro Max",
        "iPhone Duo",
    }
    set_config(
        db,
        catalog.PRODUCT_CATALOG_KEY,
        {
            "products": [
                {
                    "part_number": "STOCKCH/A",
                    "name": "普通现货",
                    "category": "iphone",
                }
            ]
        },
    )
    assert "STOCKCH/A" not in {p["part_number"] for p in catalog.list_products("iphone", db)}


def test_current_stock_candidates_without_shortage_are_excluded():
    assert "MJYL4CH/A" not in {p["part_number"] for p in OFFICIAL_PRODUCTS}
    assert any(
        p.get("part_number") == "MJYL4CH/A" and p["reason"] == "no_shortage_signal_in_sample"
        for p in CATALOG["excluded"]
    )
