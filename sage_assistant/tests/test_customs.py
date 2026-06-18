from __future__ import annotations

from decimal import Decimal

from app.customs import (
    MAX_PARCEL_WEIGHT_KG,
    CustomsParcel,
    build_customs_draft,
    build_laposte_script,
    customs_line_from_invoice_line,
)
from app.db import Database
from app.models import InvoiceLine, Product


def test_customs_line_uses_product_weight_and_invoice_value():
    line = InvoiceLine(
        ref="FL395-2",
        sage_code="RO",
        description="ROBE / TUNIC FL395-2",
        quantity_pieces=12,
        unit_price_ht=Decimal("5.50"),
    )
    product = Product(
        id=1,
        ref="FL395-2",
        type_label="ROBES COURTES",
        name="Robe",
        unit_price_ht=Decimal("5.50"),
        package_size=12,
        weight_grams=310,
        origin_country="China",
    )

    customs_line = customs_line_from_invoice_line(line, product)

    assert customs_line.unit_weight_kg == Decimal("0.310")
    assert customs_line.total_weight_kg == Decimal("3.720")
    assert customs_line.total_value_ht == Decimal("66.00")
    assert customs_line.origin_country == "China"
    assert customs_line.hs_number == "62044300"


def test_customs_line_falls_back_to_sage_code_weight():
    line = InvoiceLine(ref="PANT-1", sage_code="PA", description="Pantalon", quantity_pieces=10, unit_price_ht=Decimal("4.20"))

    customs_line = customs_line_from_invoice_line(line, None)

    assert customs_line.unit_weight_kg == Decimal("0.200")
    assert customs_line.total_weight_kg == Decimal("2.000")
    assert customs_line.total_value_ht == Decimal("42.00")


def test_parcel_reports_weight_over_30kg_and_over_real_limit():
    line = InvoiceLine(ref="FL395-2", sage_code="RO", description="Robe", quantity_pieces=101, unit_price_ht=Decimal("5.50"))
    customs_line = customs_line_from_invoice_line(line, None)
    parcel = CustomsParcel(max_weight_kg=MAX_PARCEL_WEIGHT_KG, lines=[customs_line])

    assert parcel.total_weight_kg == Decimal("30.300")
    assert parcel.weight_errors() == ["Colis 1: poids declare 30.300 kg > plafond 30.00 kg"]

    parcel.max_weight_kg = Decimal("8.00")
    assert parcel.weight_errors() == ["Colis 1: poids declare 30.300 kg > plafond 8.00 kg"]


def test_customs_declaration_persists_in_database(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    line = InvoiceLine(ref="FL395-2", sage_code="RO", description="Robe", quantity_pieces=12, unit_price_ht=Decimal("5.50"))
    draft = build_customs_draft("Microstore", "1001", [line], {}, {})

    db.save_customs_declaration(draft)
    db.close()

    reopened = Database(tmp_path / "app.sqlite")
    saved = reopened.get_customs_declaration("Microstore", "1001")

    assert saved is not None
    assert saved.parcels[0].lines[0].ref == "FL395-2"
    assert saved.parcels[0].total_weight_kg == Decimal("3.600")
    assert saved.parcels[0].total_value_ht == Decimal("66.00")
    reopened.close()


def test_laposte_script_fills_but_does_not_add_to_cart():
    line = InvoiceLine(ref="FL395-2", sage_code="RO", description="Robe", quantity_pieces=12, unit_price_ht=Decimal("5.50"))
    draft = build_customs_draft("Microstore", "1001", [line], {}, {})

    script = build_laposte_script(draft.parcels[0], draft.parcel_content)

    assert "Commencer votre déclaration" in script
    assert "Enregistrer cet objet" in script
    assert "Ajouter au panier" not in script
    assert "62044300" in script
