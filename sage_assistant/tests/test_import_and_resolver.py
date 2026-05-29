from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
import pytest

from app.db import Database
from app.excel_import import OrderRow, import_order, import_products
from app.models import Product, SageMapping
from app.resolver import Resolver


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


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


def test_import_real_microstore_xls_1001627_when_available():
    order_path = WORKSPACE_ROOT / "1001627.xls"
    if not order_path.exists():
        pytest.skip("1001627.xls absent du workspace")

    result = import_order(order_path)

    assert len(result.rows) == 7
    assert sum(row.package_count for row in result.rows) == 8
    assert sum(row.quantity_pieces or 0 for row in result.rows) == 96
    assert sum((row.unit_price_ht or Decimal("0")) * (row.quantity_pieces or 0) for row in result.rows) == Decimal("492.0")

    first = result.rows[0]
    assert first.ref == "CM55-9"
    assert first.package_count == 2
    assert first.package_size == 12
    assert first.quantity_pieces == 24
    assert first.unit_price_ht == Decimal("6.5")

    discounted = [row for row in result.rows if row.ref == "FL96-9"][0]
    assert discounted.package_count == 1
    assert discounted.package_size == 12
    assert discounted.quantity_pieces == 12
    assert discounted.unit_price_ht == Decimal("4.0")


def test_real_microstore_xls_resolves_against_real_product_export_when_available(tmp_path):
    order_path = WORKSPACE_ROOT / "1001627.xls"
    product_path = WORKSPACE_ROOT / "Modèle d_article-1779995448367743.xlsx"
    if not order_path.exists() or not product_path.exists():
        pytest.skip("fichiers Microstore reels absents du workspace")

    db = Database(tmp_path / "app.sqlite")
    product_result = import_products(product_path)
    db.upsert_products(product_result.rows)
    db.upsert_mapping(SageMapping("ROBES COURTES", "RO", "ROBE / TUNIC"))
    db.upsert_mapping(SageMapping("ROBES LONGUES", "RO", "ROBE / TUNIC"))

    order_result = import_order(order_path)
    lines = [Resolver(db).line_from_order_row(row) for row in order_result.rows]

    assert all(line.product_id for line in lines)
    assert all(line.sage_code == "RO" for line in lines)
    assert sum(line.quantity_pieces for line in lines) == 96
    fl96 = [line for line in lines if line.ref == "FL96-9"][0]
    assert fl96.unit_price_ht == Decimal("4.0")
    assert fl96.catalog_unit_price_ht == Decimal("5.0")
    assert fl96.validation_status == "blocked"
    assert "ecart prix" in fl96.validation_message
    db.close()


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
