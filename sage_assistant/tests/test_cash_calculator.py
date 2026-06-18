from __future__ import annotations

from decimal import Decimal

import pytest

from app.cash_calculator import (
    ARTDIVERS_CODE,
    ARTDIVERS_SAGE_CODE,
    adjusted_cash_for_artdivers_match,
    build_artdivers_line,
    calculate_cash_vat,
    cash_calculator_allowed,
    quantity_option,
    simple_quantity_options,
    suggest_cash_amounts,
    unit_price_matches_target,
)
from app.db import Database
from app.injection import write_injection_queue
from app.models import InvoiceLine
from app.settings import AppSettings
from app.portal_orders import PortalOrder, PortalOrderLine, PortalOrderSummary


def test_cash_vat_calculates_remaining_vat_and_total():
    result = calculate_cash_vat(Decimal("2258.40"), Decimal("100"), Decimal("20"), vat_enabled=True)

    assert result.remaining_ht == Decimal("2158.40")
    assert result.vat_amount == Decimal("431.68")
    assert result.invoice_total == Decimal("2590.08")


def test_cash_vat_without_vat_returns_remaining_ht_only():
    result = calculate_cash_vat(Decimal("2258.40"), Decimal("100"), Decimal("20"), vat_enabled=False)

    assert result.remaining_ht == Decimal("2158.40")
    assert result.vat_amount == Decimal("0.00")
    assert result.invoice_total == Decimal("2158.40")


def test_cash_vat_allows_cash_equal_to_ht():
    result = calculate_cash_vat(Decimal("2258.40"), Decimal("2258.40"), Decimal("20"), vat_enabled=True)

    assert result.remaining_ht == Decimal("0.00")
    assert result.vat_amount == Decimal("0.00")
    assert result.invoice_total == Decimal("0.00")


def test_cash_vat_refuses_cash_above_ht():
    with pytest.raises(ValueError):
        calculate_cash_vat(Decimal("2258.40"), Decimal("2258.41"), Decimal("20"), vat_enabled=True)


def test_quantity_option_reports_exact_two_decimal_match():
    option = quantity_option(Decimal("280.00"), 70)

    assert option.quantity == 70
    assert option.unit_price_ht == Decimal("4.00")
    assert option.line_total_ht == Decimal("280.00")
    assert option.exact


def test_simple_quantity_options_include_total_pieces_and_custom():
    options = simple_quantity_options(Decimal("280.00"), total_pieces=24)

    assert any(option.quantity == 24 and option.mode == "total_pieces" for option in options)
    assert any(option.quantity == 70 and option.unit_price_ht == Decimal("4.00") for option in options)


def test_cash_suggestions_are_valid_and_prioritize_exact_results():
    desired = Decimal("100.00")
    suggestions = suggest_cash_amounts(Decimal("380.00"), desired, total_pieces=24)

    assert suggestions
    assert all(Decimal("0.00") <= suggestion.cash_amount <= Decimal("380.00") for suggestion in suggestions)
    assert all(abs(suggestion.cash_amount - desired) <= Decimal("15.00") for suggestion in suggestions)
    assert suggestions[0].difference == Decimal("0.00")


def test_cash_suggestions_respect_configured_cash_flex():
    desired = Decimal("100.00")
    suggestions = suggest_cash_amounts(
        Decimal("380.00"),
        desired,
        total_pieces=24,
        target_unit_price_ht=Decimal("4.00"),
        target_quantity=70,
        cash_flex_eur=Decimal("5.00"),
    )

    assert suggestions
    assert all(abs(suggestion.cash_amount - desired) <= Decimal("5.00") for suggestion in suggestions)


def test_cash_suggestions_filter_unit_prices_around_target():
    suggestions = suggest_cash_amounts(Decimal("380.00"), Decimal("100.00"), total_pieces=24, target_unit_price_ht=Decimal("4.00"), target_quantity=70)

    assert suggestions
    assert all(65 <= suggestion.quantity <= 75 for suggestion in suggestions)
    assert all(unit_price_matches_target(suggestion.unit_price_ht, Decimal("4.00")) for suggestion in suggestions)


def test_cash_suggestions_can_keep_total_pieces_exact():
    suggestions = suggest_cash_amounts(
        Decimal("380.00"),
        Decimal("284.00"),
        total_pieces=24,
        target_unit_price_ht=Decimal("4.00"),
        target_quantity=24,
        target_quantity_flex=0,
    )

    assert suggestions
    assert all(suggestion.quantity == 24 for suggestion in suggestions)


def test_cash_suggestions_accept_one_decimal_flex_around_target():
    suggestions = suggest_cash_amounts(Decimal("380.00"), Decimal("100.00"), total_pieces=24, target_unit_price_ht=Decimal("4.00"), target_quantity=70, limit=50)

    assert unit_price_matches_target(Decimal("3.50"), Decimal("4.00"))
    assert unit_price_matches_target(Decimal("4.50"), Decimal("4.00"))
    assert not unit_price_matches_target(Decimal("3.49"), Decimal("4.00"))
    assert all(int((suggestion.unit_price_ht * 100).to_integral_value()) % 10 == 0 for suggestion in suggestions)


def test_manual_quantity_can_still_be_below_minimum():
    option = quantity_option(Decimal("100.00"), 200)

    assert option.unit_price_ht == Decimal("0.50")
    assert option.exact


def test_adjusted_cash_for_artdivers_match_offsets_rounding_difference():
    adjusted = adjusted_cash_for_artdivers_match(Decimal("100.00"), Decimal("0.00"), Decimal("100.00"), 33)

    assert adjusted == Decimal("0.01")
    artdivers = build_artdivers_line(
        [InvoiceLine(ref="FL395-2", sage_code="RO", description="Robe", quantity_pieces=1, unit_price_ht=Decimal("100.00"), product_id=1)],
        Decimal("99.99"),
        33,
        source="Microstore",
    )
    assert artdivers.unit_price_ht == Decimal("3.03")


def test_build_artdivers_line_uses_refs_only_and_exact_total():
    lines = [
        InvoiceLine(ref="FL395-2", sage_code="RO", description="Robe", quantity_pieces=12, unit_price_ht=Decimal("6.00"), product_id=1),
        InvoiceLine(ref="FL720-2", sage_code="TO", description="Top", quantity_pieces=8, unit_price_ht=Decimal("5.00"), product_id=2),
    ]
    for line in lines:
        line.validate()

    artdivers = build_artdivers_line(lines, Decimal("280.00"), 70, source="Microstore")

    assert artdivers.ref == ARTDIVERS_CODE
    assert artdivers.sage_code == ARTDIVERS_SAGE_CODE
    assert artdivers.sage_code == "Article D"
    assert artdivers.description == "FL395-2 FL720-2"
    assert artdivers.quantity_pieces == 70
    assert artdivers.unit_price_ht == Decimal("4.00")
    assert artdivers.validation_status == "ok"


def test_build_artdivers_line_keeps_cash_memory():
    lines = [InvoiceLine(ref="FL395-2", sage_code="RO", description="Robe", quantity_pieces=168, unit_price_ht=Decimal("4.78"), product_id=1)]

    artdivers = build_artdivers_line(
        lines,
        Decimal("300.72"),
        168,
        source="Microstore",
        cash_reference_ht=Decimal("802.80"),
        cash_amount=Decimal("502.08"),
        cash_vat_rate=Decimal("20"),
        cash_vat_enabled=True,
        cash_quantity_mode="total_pieces",
        cash_original_refs="FL395-2",
    )

    assert artdivers.cash_reference_ht == Decimal("802.80")
    assert artdivers.cash_amount == Decimal("502.08")
    assert artdivers.cash_target_quantity == 168
    assert artdivers.cash_quantity_mode == "total_pieces"
    assert artdivers.cash_original_refs == "FL395-2"


def test_artdivers_line_injects_article_d_for_sage_autocomplete(tmp_path):
    original = [InvoiceLine(ref="FL395-2", sage_code="RO", description="Robe", quantity_pieces=10, unit_price_ht=Decimal("38.00"), product_id=1)]
    artdivers = build_artdivers_line(original, Decimal("280.00"), 70, source="Microstore")

    queue_path = write_injection_queue([artdivers], AppSettings(), tmp_path / "queue.json")

    assert '"article_code": "Article D"' in queue_path.read_text(encoding="utf-8")


def test_artdivers_line_can_be_saved_as_order_edit(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    summary = PortalOrderSummary(source="Microstore", order_id="1001", order_number="1001", customer="Client", total_amount=Decimal("380.00"))
    detail = PortalOrder(
        source="Microstore",
        order_id="1001",
        order_number="1001",
        customer="Client",
        lines=[PortalOrderLine(ref="FL395-2", quantity_pieces=10, unit_price_ht=Decimal("38.00"))],
    )
    db.upsert_cached_order(summary, detail, "Pret")
    original = [InvoiceLine(ref="FL395-2", sage_code="RO", description="Robe", quantity_pieces=10, unit_price_ht=Decimal("38.00"), product_id=1)]
    artdivers = build_artdivers_line(original, Decimal("280.00"), 70, source="Microstore")

    db.save_order_line_edits("Microstore", "1001", [artdivers], "Pret")
    saved = db.get_order_line_edits("Microstore", "1001")

    assert saved is not None
    assert saved[0].sage_code == ARTDIVERS_SAGE_CODE
    assert saved[0].description == "FL395-2"
    assert saved[0].quantity_pieces == 70
    assert saved[0].unit_price_ht == Decimal("4.00")
    db.close()


def test_artdivers_cash_memory_is_persisted_with_order_edits(tmp_path):
    db = Database(tmp_path / "app.sqlite")
    summary = PortalOrderSummary(source="Microstore", order_id="1002", order_number="1002", customer="Client", total_amount=Decimal("802.80"))
    detail = PortalOrder(source="Microstore", order_id="1002", order_number="1002", customer="Client", lines=[])
    db.upsert_cached_order(summary, detail, "Pret")
    artdivers = build_artdivers_line(
        [InvoiceLine(ref="FL395-2", sage_code="RO", description="Robe", quantity_pieces=168, unit_price_ht=Decimal("4.78"), product_id=1)],
        Decimal("300.72"),
        168,
        source="Microstore",
        cash_reference_ht=Decimal("802.80"),
        cash_amount=Decimal("502.08"),
        cash_vat_rate=Decimal("20"),
        cash_vat_enabled=True,
        cash_quantity_mode="total_pieces",
        cash_original_refs="FL395-2",
    )

    db.save_order_line_edits("Microstore", "1002", [artdivers], "Pret")
    saved = db.get_order_line_edits("Microstore", "1002")

    assert saved is not None
    assert saved[0].cash_reference_ht == Decimal("802.80")
    assert saved[0].cash_amount == Decimal("502.08")
    assert saved[0].cash_target_quantity == 168
    assert saved[0].cash_vat_enabled is True
    db.close()


def test_cash_calculator_allowed_sources():
    assert cash_calculator_allowed("Microstore")
    assert cash_calculator_allowed("Fichier manuel")
    assert not cash_calculator_allowed("PFS")
    assert not cash_calculator_allowed("eFashion")
