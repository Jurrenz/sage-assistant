from __future__ import annotations

from decimal import Decimal

from openpyxl import Workbook

from app.db import Database
from app.excel_import import OrderRow, import_order, import_products
from app.models import Product, SageMapping
from app.resolver import Resolver


def test_import_products_and_resolve_line(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Note : export Microstore manuel"])
    sheet.append(["reference", "type", "nom", "prix", "colisage"])
    sheet.append([" FL530-1 ", "ROBES COURTES", "Robe test", "6,80", 12])
    path = tmp_path / "products.xlsx"
    workbook.save(path)

    result = import_products(path)
    db = Database(tmp_path / "app.sqlite")
    db.upsert_products(result.rows)
    db.upsert_mapping(SageMapping("ROBES COURTES", "RO", "ROBE / TUNIC"))

    product = db.get_product_by_ref("FL530-1")
    assert product is not None
    assert product.ref == "FL530-1"
    assert product.unit_price_ht == Decimal("6.80")
    assert product.package_size == 12

    line = Resolver(db).line_from_product(product, quantity_pieces=24, package_count=2)
    assert line.validation_status == "ok"
    assert line.sage_code == "RO"
    assert line.description == "FL530-1"
    assert line.quantity_pieces == 24
    db.close()


def test_import_order_reads_packages_and_calculates_pieces(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["No commande", "No de produits", "qte", "Colisage", "Nombre de pieces", "Prix unitaire"])
    sheet.append(["1001", "LA15-9", 3, 12, 36, "4.20"])
    path = tmp_path / "order.xlsx"
    workbook.save(path)

    result = import_order(path)
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.ref == "LA15-9"
    assert row.package_count == 3
    assert row.package_size == 12
    assert row.quantity_pieces == 36
    assert row.unit_price_ht == Decimal("4.20")


def test_order_line_price_mismatch_requires_confirmation(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    db.upsert_products(
        [
            Product(
                id=None,
                ref="LA15-9",
                type_label="ROBES LONGUES",
                name="",
                unit_price_ht=Decimal("5.00"),
                package_size=12,
            )
        ]
    )
    db.upsert_mapping(SageMapping("ROBES LONGUES", "RO", "ROBE / TUNIC"))

    row = OrderRow(
        ref="LA15-9",
        package_count=3,
        package_size=12,
        quantity_pieces=36,
        unit_price_ht=Decimal("4.20"),
    )
    line = Resolver(db).line_from_order_row(row)

    assert line.quantity_pieces == 36
    assert line.unit_price_ht == Decimal("4.20")
    assert line.catalog_unit_price_ht == Decimal("5.00")
    assert line.order_unit_price_ht == Decimal("4.20")
    assert line.validation_status == "blocked"
    assert "ecart prix" in line.validation_message
    db.close()
