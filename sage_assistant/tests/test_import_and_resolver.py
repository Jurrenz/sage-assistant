from __future__ import annotations

from decimal import Decimal

from openpyxl import Workbook

from app.db import Database
from app.excel_import import import_order, import_products
from app.models import SageMapping
from app.resolver import Resolver


def test_import_products_and_resolve_line(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["reference", "type", "nom", "prix", "colisage"])
    sheet.append(["FL530-1", "ROBES COURTES", "Robe test", "6,80", 12])
    path = tmp_path / "products.xlsx"
    workbook.save(path)

    result = import_products(path)
    db = Database(tmp_path / "app.sqlite")
    db.upsert_products(result.rows)
    db.upsert_mapping(SageMapping("ROBES COURTES", "RO", "ROBE / TUNIC"))

    product = db.get_product_by_ref("FL530-1")
    assert product is not None
    assert product.unit_price_ht == Decimal("6.80")
    assert product.package_size == 12

    line = Resolver(db).line_from_product(product, quantity_pieces=24, package_count=2)
    assert line.validation_status == "ok"
    assert line.sage_code == "RO"
    assert line.description == "ROBE / TUNIC FL530-1"
    assert line.quantity_pieces == 24


def test_import_order_uses_microstore_price(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["ref", "qte", "prix"])
    sheet.append(["LA15-9", 3, "4.20"])
    path = tmp_path / "order.xlsx"
    workbook.save(path)

    result = import_order(path)
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.ref == "LA15-9"
    assert row.quantity == 3
    assert row.unit_price_ht == Decimal("4.20")
