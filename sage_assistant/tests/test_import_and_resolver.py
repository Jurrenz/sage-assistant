from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
import pytest

from app.db import Database
from app.excel_import import OrderRow, import_order, import_products
from app.sage_pdf_import import import_sage_pdf_invoice, parse_sage_pdf_text
from app.main import (
    QUICK_INVOICE_SOURCE,
    line_headers_for_source,
    lines_with_saved_order_edits,
    parse_quick_ref_text,
    quick_invoice_line_from_clipboard_cells,
    quick_invoice_line_to_clipboard_row,
    quick_invoice_to_portal_order,
)
from app.microstore_product_writer import MicrostoreProductWriter
from app.models import InvoiceLine, Product, SageMapping, build_sage_description
from app.portal_orders import PortalClient, PortalOrder, PortalOrderLine, PortalOrderSummary
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

    product = db.get_product_by_ref("FL530-1")
    assert product is not None
    assert product.ref == "FL530-1"
    assert product.unit_price_ht == Decimal("6.80")
    assert product.package_size == 12

    line = Resolver(db).line_from_product(product, quantity_pieces=24, package_count=2)
    assert line.validation_status == "ok"
    assert line.sage_code == "RO"
    assert line.description == "ROBE / TUNIC FL530-1"
    assert line.quantity_pieces == 24
    db.close()


def test_default_sage_mappings_are_seeded_and_do_not_overwrite_user_edits(tmp_path):
    db = Database(tmp_path / "app.sqlite")

    mapping = db.get_mapping("CHEMISES / TUNIQUES")
    assert mapping is not None
    assert mapping.sage_code == "CH"
    assert mapping.sage_label == "Chemise"

    db.upsert_mapping(SageMapping("CROCHETS", "PU", "Pull / Gilet"))
    inserted = db.seed_default_mappings()
    edited_mapping = db.get_mapping("CROCHETS")

    assert inserted == 0
    assert edited_mapping is not None
    assert edited_mapping.sage_code == "PU"
    assert edited_mapping.sage_label == "Pull / Gilet"
    db.close()


def test_restore_default_mappings_reactivates_without_overwriting_user_edits(tmp_path):
    db = Database(tmp_path / "app.sqlite")

    db.upsert_mapping(SageMapping("CROCHETS", "PU", "Pull / Gilet"))
    db.deactivate_mapping("CROCHETS")
    restored = db.restore_default_mappings()
    restored_mapping = db.get_mapping("CROCHETS")

    assert restored == 1
    assert restored_mapping is not None
    assert restored_mapping.is_active is True
    assert restored_mapping.sage_code == "PU"
    assert restored_mapping.sage_label == "Pull / Gilet"
    db.close()


def test_restore_default_mappings_repairs_labels_accidentally_saved_as_codes(tmp_path):
    db = Database(tmp_path / "app.sqlite")

    db.upsert_mapping(SageMapping("SHORTS", "SH", "SH"))
    restored = db.restore_default_mappings()
    restored_mapping = db.get_mapping("SHORTS")

    assert restored == 1
    assert restored_mapping is not None
    assert restored_mapping.sage_code == "SH"
    assert restored_mapping.sage_label == "Short"
    db.close()


def test_order_statuses_are_persistent(tmp_path):
    db = Database(tmp_path / "app.sqlite")

    assert db.get_order_status("Microstore", "1001627") is None

    db.set_order_status("Microstore", "1001627", "Injecté")
    assert db.get_order_status("Microstore", "1001627") == "Injecté"

    db.set_order_status("Microstore", "1001627", "Traité")
    assert db.get_order_status("Microstore", "1001627") == "Traité"
    db.close()


def test_deactivate_mapping_hides_it_and_blocks_resolution(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    db.upsert_mapping(SageMapping("ROBES TEST", "RO", "Robe"))

    assert db.get_mapping("ROBES TEST") is not None

    db.deactivate_mapping("ROBES TEST")

    assert db.get_mapping("ROBES TEST") is None
    assert all(mapping.microstore_type != "ROBES TEST" for mapping in db.list_mappings())
    inactive = [mapping for mapping in db.list_mappings(active_only=False) if mapping.microstore_type == "ROBES TEST"]
    assert inactive and inactive[0].is_active is False
    db.close()


def test_parse_quick_ref_text_defaults_to_one_package():
    assert parse_quick_ref_text("FL530-1") == ("FL530-1", 1)
    assert parse_quick_ref_text("fl530-1 x2") == ("FL530-1", 2)
    assert parse_quick_ref_text("LA15-9 ×3") == ("LA15-9", 3)
    assert parse_quick_ref_text("CM55-9\t4") == ("CM55-9", 4)
    assert parse_quick_ref_text("CM55-9\tROBE\t4") == ("CM55-9", 4)


def test_parse_sage_pdf_text_extracts_invoice_lines_and_ignores_tax_line():
    text = """
IndiceArticle Description Qté P.U. HTMt Tot HT TVA Mt Tot TTC
1 PA Pantalon G05-3 24,00 3,20 76,80 0 76,80
2 RO ROBE / TUNIC FL313-25 24,00 6,00 144,00 0 144,00
3 EN Ensemble 2pcs FL779 12,00 9,00 108,00 0 108,00
4 EXP Exonération TVA ART262-1 TER2 CGI 1,00 0
Facture N°
FA2551
Date
25/03/2026
"""

    invoice = parse_sage_pdf_text(text)

    assert invoice.invoice_number == "FA2551"
    assert invoice.invoice_date == "25/03/2026"
    assert len(invoice.lines) == 3
    assert invoice.lines[0].sage_code == "PA"
    assert invoice.lines[0].ref == "G05-3"
    assert invoice.lines[0].quantity_pieces == 24
    assert invoice.lines[0].unit_price_ht == Decimal("3.20")
    assert invoice.lines[2].ref == "FL779"


def test_import_real_sage_pdf_invoice_when_available():
    pdf_path = Path(r"C:\Documents\Format A4~1.pdf")
    if not pdf_path.exists():
        pytest.skip("PDF Sage exemple absent")

    invoice = import_sage_pdf_invoice(pdf_path)

    assert invoice.invoice_number == "FA2551"
    assert len(invoice.lines) == 13
    assert sum(line.quantity_pieces for line in invoice.lines) == 312
    assert sum(line.quantity_pieces * line.unit_price_ht for line in invoice.lines) == Decimal("1840.80")


def test_quick_invoice_line_uses_one_package_by_default(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    db.upsert_products(
        [
            Product(
                id=None,
                ref="FL530-1",
                type_label="ROBES COURTES",
                name="Robe test",
                unit_price_ht=Decimal("6.80"),
                package_size=12,
            )
        ]
    )
    db.upsert_mapping(SageMapping("ROBES COURTES", "RO", "ROBE / TUNIC"))

    product = db.get_product_by_ref("FL530-1")
    line = Resolver(db).line_from_product(product, quantity_pieces=product.package_size, package_count=1, source="quick_invoice")

    assert line.validation_status == "ok"
    assert line.package_count == 1
    assert line.package_size == 12
    assert line.quantity_pieces == 12
    assert line.unit_price_ht == Decimal("6.80")
    assert line.sage_code == "RO"
    db.close()


def test_sage_description_normalizes_mapping_label_spaces():
    assert build_sage_description("A01-11", "Short ") == "Short A01-11"
    assert build_sage_description("FR", "Frais de port ") == "Frais de port FR"
    assert build_sage_description("FL530-1", "") == "FL530-1"


def test_line_headers_show_sage_and_catalog_prices_by_source():
    assert line_headers_for_source("Microstore")[6] == "Prix Sage"
    assert line_headers_for_source(QUICK_INVOICE_SOURCE)[6] == "Prix Sage"
    assert line_headers_for_source("PFS")[6] == "Prix commande"
    assert line_headers_for_source("eFashion")[6] == "Prix commande"
    assert line_headers_for_source("PFS")[7] == "Prix Microstore"


def test_quick_invoice_can_be_persisted_as_cached_order(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    line = Resolver(db).line_from_product(
        Product(
            id=12,
            ref="FL530-1",
            type_label="ROBES COURTES",
            name="Robe test",
            unit_price_ht=Decimal("6.80"),
            package_size=12,
        ),
        quantity_pieces=24,
        package_count=2,
        source="quick_invoice",
    )
    line.sage_code = "RO"
    line.validate()

    summary, detail = quick_invoice_to_portal_order([line], order_number="FR-TEST", created_at="2026-06-10T10:00:00Z")
    db.upsert_cached_order(summary, detail, "Prêt")
    db.close()

    reopened = Database(tmp_path / "app.sqlite")
    cached_summary = reopened.list_cached_order_summaries()[0]
    cached_order = reopened.get_cached_order(QUICK_INVOICE_SOURCE, "FR-TEST")

    assert cached_summary.source == QUICK_INVOICE_SOURCE
    assert cached_summary.order_number == "FR-TEST"
    assert cached_summary.customer == "Facture rapide"
    assert cached_summary.total_amount == Decimal("163.20")
    assert reopened.count_cached_orders(QUICK_INVOICE_SOURCE) == 1
    assert cached_order is not None
    assert cached_order.lines[0].ref == "FL530-1"
    assert cached_order.lines[0].package_count == 2
    assert cached_order.lines[0].quantity_pieces == 24
    assert cached_order.lines[0].unit_price_ht == Decimal("6.80")
    assert cached_order.lines[0].raw["sage_code"] == "RO"
    reopened.close()


def test_order_line_edits_are_persistent_and_resettable(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    summary = PortalOrderSummary(
        source="Microstore",
        order_id="1001",
        order_number="1001",
        customer="Client test",
        total_amount=Decimal("12.00"),
    )
    detail = PortalOrder(
        source="Microstore",
        order_id="1001",
        order_number="1001",
        customer="Client test",
        lines=[
            PortalOrderLine(
                ref="FL530-1",
                category="ROBES COURTES",
                package_count=1,
                package_size=12,
                quantity_pieces=12,
                unit_price_ht=Decimal("6.80"),
            )
        ],
    )
    db.upsert_cached_order(summary, detail, "Prêt")
    edited = InvoiceLine(
        ref="FL530-1",
        sage_code="RO",
        description="ROBE / TUNIC FL530-1",
        quantity_pieces=24,
        package_count=2,
        package_size=12,
        unit_price_ht=Decimal("6.80"),
        catalog_unit_price_ht=Decimal("6.80"),
        order_unit_price_ht=Decimal("6.80"),
        product_id=12,
        type_label="ROBES COURTES",
        source="Microstore",
    )
    edited.validate()
    db.save_order_line_edits("Microstore", "1001", [edited], "Prêt")
    db.close()

    reopened = Database(tmp_path / "app.sqlite")
    edits = reopened.get_order_line_edits("Microstore", "1001")

    assert edits is not None
    assert edits[0].quantity_pieces == 24
    assert edits[0].sage_code == "RO"
    assert reopened.list_cached_order_statuses()[("Microstore", "1001")] == "Prêt"
    assert reopened.list_cached_order_summaries()[0].total_amount == Decimal("163.20")

    reopened.clear_order_line_edits("Microstore", "1001")
    assert reopened.get_order_line_edits("Microstore", "1001") is None
    cached = reopened.get_cached_order("Microstore", "1001")
    assert cached is not None
    assert cached.lines[0].quantity_pieces == 12
    reopened.close()


def test_saved_order_edits_override_fallback_lines_before_injection(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    fallback = [
        InvoiceLine(
            ref="FL530-1",
            sage_code="RO",
            description="Original",
            quantity_pieces=12,
            unit_price_ht=Decimal("6.80"),
            product_id=1,
            type_label="ROBES COURTES",
            source="Microstore",
        )
    ]
    edited = [
        InvoiceLine(
            ref="FL530-1",
            sage_code="RO",
            description="Corrigé",
            quantity_pieces=24,
            unit_price_ht=Decimal("6.80"),
            product_id=1,
            type_label="ROBES COURTES",
            source="Microstore",
        )
    ]
    for line in [*fallback, *edited]:
        line.validate()
    db.save_order_line_edits("Microstore", "1001", edited, "Prêt")

    selected = lines_with_saved_order_edits(db, "Microstore", "1001", fallback)

    assert selected[0].description == "Corrigé"
    assert selected[0].quantity_pieces == 24
    db.close()


def test_quick_invoice_clipboard_roundtrip_preserves_editable_details():
    invoice_line = InvoiceLine(
        ref="FL530-1",
        sage_code="RO",
        description="ROBE / TUNIC FL530-1 CORRIGEE",
        quantity_pieces=30,
        package_count=2,
        package_size=15,
        unit_price_ht=Decimal("7.10"),
        catalog_unit_price_ht=Decimal("6.80"),
        order_unit_price_ht=Decimal("7.10"),
        product_id=12,
        type_label="ROBES COURTES",
        source="quick_invoice",
    )
    invoice_line.validate()

    copied = quick_invoice_line_to_clipboard_row(invoice_line)
    pasted = quick_invoice_line_from_clipboard_cells(copied)

    assert pasted is not None
    assert pasted.ref == "FL530-1"
    assert pasted.sage_code == "RO"
    assert pasted.description == "ROBE / TUNIC FL530-1 CORRIGEE"
    assert pasted.package_count == 2
    assert pasted.package_size == 15
    assert pasted.quantity_pieces == 30
    assert pasted.unit_price_ht == Decimal("7.10")
    assert pasted.catalog_unit_price_ht == Decimal("6.80")
    assert pasted.validation_message == "reference non resolue"


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
    assert fl96.price_confirmed is False
    assert fl96.validation_status == "ok"
    assert "ecart prix" not in fl96.validation_message
    db.close()


def test_order_line_price_mismatch_is_informational(tmp_path):
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
    assert line.price_confirmed is False
    assert line.validation_status == "ok"
    assert "ecart prix" not in line.validation_message
    db.close()


def test_disabled_microstore_product_can_resolve_for_sage(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    db.upsert_products(
        [
            Product(
                id=None,
                ref="JY96",
                type_label="ROBES LONGUES",
                name="",
                unit_price_ht=Decimal("7.50"),
                package_size=12,
                microstore_status="disabled",
            )
        ]
    )
    db.upsert_mapping(SageMapping("ROBES LONGUES", "RO", "ROBE / TUNIC"))

    product = db.get_product_by_ref("JY96")
    line = Resolver(db).line_from_ref("JY96", quantity_pieces=12, unit_price_ht=Decimal("7.50"))

    assert product is not None
    assert product.microstore_status == "disabled"
    assert line.validation_status == "ok"
    assert line.sage_code == "RO"
    assert line.package_size == 12
    db.close()


def test_microstore_sync_marks_missing_products_as_historical_without_deleting(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    db.upsert_products(
        [
            Product(
                id=None,
                ref="JY96",
                type_label="ROBES LONGUES",
                name="Ancienne ref",
                unit_price_ht=Decimal("7.50"),
                package_size=12,
                microstore_status="active",
            ),
            Product(
                id=None,
                ref="FL530-1",
                type_label="ROBES COURTES",
                name="Robe active",
                unit_price_ht=Decimal("6.80"),
                package_size=12,
                microstore_status="active",
            ),
        ]
    )

    db.upsert_products(
        [
            Product(
                id=None,
                ref="FL530-1",
                type_label="ROBES COURTES",
                name="Robe active",
                unit_price_ht=Decimal("6.80"),
                package_size=12,
                microstore_status="active",
            )
        ],
        mark_missing=True,
    )

    missing = db.get_product_by_ref("JY96")
    active = db.get_product_by_ref("FL530-1")

    assert missing is not None
    assert missing.microstore_status == "absent"
    assert missing.workflow_status == "historical"
    assert active is not None
    assert active.microstore_status == "active"
    assert active.workflow_status == "synced"
    db.close()


def test_product_draft_does_not_get_marked_missing_by_microstore_sync(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    draft = db.upsert_product_draft(
        Product(
            id=None,
            ref="NEW123",
            type_label="ROBES COURTES",
            name="Nouveau produit",
            unit_price_ht=Decimal("8.00"),
            package_size=12,
            workflow_status="draft",
        )
    )

    db.upsert_products([], mark_missing=True)
    reloaded = db.get_product_by_ref("NEW123")
    preview = db.product_change_preview(draft)

    assert reloaded is not None
    assert reloaded.workflow_status == "to_create"
    assert reloaded.microstore_status == ""
    assert preview[0] == "Créer NEW123 dans Microstore (simulation)"
    db.close()


def test_product_activity_dates_and_sheet_fields_are_persisted(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    db.upsert_products(
        [
            Product(
                id=None,
                ref="FL999",
                type_label="ROBES COURTES",
                name="Produit test",
                unit_price_ht=Decimal("9.50"),
                package_size=12,
                microstore_status="active",
                brand="SZ",
                year="2026",
                season="ETE",
                origin_country="China",
                stock_snapshot=24,
                promo="PROMO",
                discount_percent=Decimal("10"),
                remark="Note produit",
                platform_price_ht=Decimal("10.00"),
                last_microstore_modified_at="2026-06-01T10:00:00Z",
            )
        ]
    )

    product = db.get_product_by_ref("FL999")

    assert product is not None
    assert product.brand == "SZ"
    assert product.year == "2026"
    assert product.season == "ETE"
    assert product.origin_country == "China"
    assert product.stock_snapshot == 24
    assert product.discount_percent == Decimal("10")
    assert product.platform_price_ht == Decimal("10.00")
    assert product.last_microstore_modified_at == "2026-06-01T10:00:00Z"
    db.close()


class FakeMicrostoreProductConnector:
    def __init__(self) -> None:
        self.add_payloads = []
        self.update_payloads = []
        self.status_calls = []

    def add_product(self, payload):
        self.add_payloads.append(payload)
        return Product(
            id=None,
            ref=payload["item_ref"],
            type_label="ROBES COURTES",
            name=payload["name"],
            unit_price_ht=Decimal("8.00"),
            package_size=12,
            workflow_status="synced",
            raw={"id": "9001"},
        )

    def update_product(self, product_id, payload):
        self.update_payloads.append((product_id, payload))
        return Product(
            id=None,
            ref=payload["item_ref"],
            type_label="ROBES COURTES",
            name=payload["name"],
            unit_price_ht=Decimal("9.00"),
            package_size=6,
            workflow_status="synced",
            raw={"id": product_id},
        )

    def set_product_active(self, product_id, active):
        self.status_calls.append((product_id, active))
        return Product(
            id=None,
            ref="OLD123",
            type_label="ROBES COURTES",
            name="Produit test",
            unit_price_ht=Decimal("9.00"),
            package_size=6,
            microstore_status="active" if active else "disabled",
            workflow_status="synced",
            raw={"id": product_id},
        )


def test_microstore_product_writer_creates_updates_and_sets_status():
    product = Product(
        id=None,
        ref="NEW123",
        type_label="ROBES COURTES",
        name="Produit test",
        unit_price_ht=Decimal("8.00"),
        package_size=12,
        workflow_status="to_create",
    )
    connector = FakeMicrostoreProductConnector()
    writer = MicrostoreProductWriter(connector)  # type: ignore[arg-type]
    payload = writer.build_payload(product)

    assert payload.endpoint == "/goods/add"
    assert payload.payload["item_ref"] == "NEW123"
    created = writer.apply(product)
    assert created.raw["id"] == "9001"
    assert connector.add_payloads[0]["sku"][0]["price_1"] == "8.00"

    updated = writer.apply(
        Product(
            id=None,
            ref="OLD123",
            type_label="ROBES COURTES",
            name="Produit modifié",
            unit_price_ht=Decimal("9.00"),
            package_size=6,
            workflow_status="modified",
            raw={"id": "42", "sku": [{"id": "55", "color_id": "9", "size_id": "0", "num_per_pack": "12"}]},
        )
    )
    assert updated.package_size == 6
    assert connector.update_payloads[0][0] == "42"
    assert connector.update_payloads[0][1]["sku"][0]["id"] == "55"

    disabled = writer.set_active(updated, False)
    assert disabled.microstore_status == "disabled"
    assert connector.status_calls == [("42", False)]


def test_cached_portal_line_with_zero_quantity_is_repaired(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    db.upsert_mapping(SageMapping("ROBES COURTES", "RO", "ROBE / TUNIC"))

    line = Resolver(db).line_from_portal_line(
        PortalOrderLine(
            ref="FL329-2",
            category="Robes",
            package_count=1,
            package_size=12,
            quantity_pieces=0,
            unit_price_ht=Decimal("4"),
        ),
        source="PFS",
    )

    assert line.quantity_pieces == 12
    assert line.validation_status == "ok"
    db.close()


def test_cached_portal_orders_are_persistent(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    summary = PortalOrderSummary(
        source="eFashion",
        order_id="42",
        order_number="EF42",
        customer="Client test",
        created_at="2026-06-05T16:48:37Z",
        status="validated",
        total_amount=Decimal("78.00"),
        raw={"id": 42},
    )
    detail = PortalOrder(
        source="eFashion",
        order_id="42",
        order_number="EF42",
        customer="Client test",
        created_at="2026-06-05T16:48:37Z",
        status="validated",
        total_amount=Decimal("78.00"),
        lines=[
            PortalOrderLine(
                ref="FL530-1",
                category="ROBES COURTES",
                package_count=1,
                package_size=12,
                quantity_pieces=12,
                unit_price_ht=Decimal("6.50"),
                raw={"reference": "FL530-1"},
            )
        ],
        raw={"detail": True},
    )

    db.upsert_cached_order(summary, detail, "Prêt")
    db.close()

    reopened = Database(tmp_path / "app.sqlite")
    summaries = reopened.list_cached_order_summaries()
    cached = reopened.get_cached_order("eFashion", "EF42")

    assert len(summaries) == 1
    assert summaries[0].customer == "Client test"
    assert reopened.list_cached_order_statuses()[("eFashion", "EF42")] == "Prêt"
    assert reopened.count_cached_orders("eFashion") == 1
    assert cached is not None
    assert cached.lines[0].ref == "FL530-1"
    assert cached.lines[0].unit_price_ht == Decimal("6.50")
    reopened.close()


def test_cached_clients_are_persistent_and_searchable(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    db.upsert_clients(
        [
            PortalClient(
                source="Microstore",
                client_id="client-1",
                client_key="client-1",
                name="Caelle Boutique",
                company="ROMIE",
                phone="0650616033",
                email="client@example.com",
                city="Sete",
                country="France",
                raw={"id": "client-1"},
            )
        ]
    )
    db.close()

    reopened = Database(tmp_path / "app.sqlite")
    clients = reopened.list_clients("Microstore", search="romie")
    assert reopened.count_clients("Microstore") == 1
    assert clients[0].company == "ROMIE"
    assert clients[0].email == "client@example.com"
    assert reopened.find_client("Microstore", "Caelle") is not None
    reopened.close()
