from __future__ import annotations

import json
from decimal import Decimal

from app.injection import write_injection_queue
from app.models import InvoiceLine
from app.settings import REAL_SAGE_ONE_LINE_MODE, SAGE_50_WINDOW_TITLE, AppSettings, load_settings, save_settings


def test_write_injection_queue(tmp_path):
    line = InvoiceLine(
        ref="FL530-1",
        sage_code="RO",
        description="ROBE / TUNIC FL530-1",
        quantity_pieces=12,
        unit_price_ht=Decimal("6.80"),
        product_id=1,
    )
    line.validate()

    queue_path = write_injection_queue([line], AppSettings(), tmp_path / "queue.json")
    payload = json.loads(queue_path.read_text(encoding="utf-8"))

    assert payload["lines"][0]["article_code"] == "RO"
    assert payload["lines"][0]["quantity"] == 12
    assert payload["lines"][0]["unit_price_ht"] == "6.80"


def test_write_injection_queue_normalizes_description_spaces(tmp_path):
    line = InvoiceLine(
        ref="A01-11",
        sage_code="SH",
        description="Short  A01-11",
        quantity_pieces=12,
        unit_price_ht=Decimal("6.80"),
        product_id=1,
    )
    line.validate()

    queue_path = write_injection_queue([line], AppSettings(), tmp_path / "queue.json")
    payload = json.loads(queue_path.read_text(encoding="utf-8"))

    assert payload["lines"][0]["description"] == "Short A01-11"


def test_write_injection_queue_default_is_temporary_file():
    line = InvoiceLine(
        ref="FL530-1",
        sage_code="RO",
        description="ROBE / TUNIC FL530-1",
        quantity_pieces=12,
        unit_price_ht=Decimal("6.80"),
        product_id=1,
    )
    line.validate()

    queue_path = write_injection_queue([line], AppSettings())

    assert queue_path.name.startswith("sage_assistant_queue_")
    assert queue_path.exists()
    queue_path.unlink()


def test_write_injection_queue_writes_all_valid_lines(tmp_path):
    lines = []
    for index in range(3):
        line = InvoiceLine(
            ref=f"FL{index}",
            sage_code="RO",
            description=f"FL{index}",
            quantity_pieces=12,
            unit_price_ht=Decimal("6.80"),
            product_id=index + 1,
        )
        line.validate()
        lines.append(line)

    settings = AppSettings()
    queue_path = write_injection_queue(lines, settings, tmp_path / "queue.json", line_limit=1)
    payload = json.loads(queue_path.read_text(encoding="utf-8"))

    assert payload["source_line_count"] == 3
    assert payload["line_limit"] == 0
    assert len(payload["lines"]) == 3
    assert payload["profile"]["step_mode"] is False
    assert payload["profile"]["focus_guard"] is True
    assert payload["profile"]["injection_mode"] == REAL_SAGE_ONE_LINE_MODE
    assert payload["profile"]["confirmation_mode"] == "simple"
    assert payload["profile"]["stable_pause_ms"] == 220
    assert "diagnostics_path" in payload["profile"]


def test_write_injection_queue_refuses_any_blocked_line(tmp_path):
    ok_line = InvoiceLine(
        ref="CM55-9",
        sage_code="RO",
        description="CM55-9",
        quantity_pieces=24,
        unit_price_ht=Decimal("6.50"),
        product_id=1,
    )
    ok_line.validate()
    blocked_line = InvoiceLine(
        ref="FL96-9",
        sage_code="",
        description="FL96-9",
        quantity_pieces=12,
        unit_price_ht=Decimal("4.00"),
        product_id=2,
    )
    blocked_line.validate()

    try:
        write_injection_queue([ok_line, blocked_line], AppSettings(), tmp_path / "queue.json", line_limit=1)
    except ValueError as exc:
        assert "FL96-9" in str(exc)
    else:
        raise AssertionError("blocked line should stop queue creation")


def test_real_sage_mode_ignores_legacy_line_limit(tmp_path):
    lines = []
    for index in range(2):
        line = InvoiceLine(
            ref=f"LA55-{index}",
            sage_code="RO",
            description=f"LA55-{index}",
            quantity_pieces=10,
            unit_price_ht=Decimal("6.50"),
            product_id=index + 1,
        )
        line.validate()
        lines.append(line)

    settings = AppSettings()
    settings.sage_profile.injection_mode = REAL_SAGE_ONE_LINE_MODE

    queue_path = write_injection_queue(lines, settings, tmp_path / "queue.json", line_limit=1)
    payload = json.loads(queue_path.read_text(encoding="utf-8"))

    assert payload["profile"]["injection_mode"] == REAL_SAGE_ONE_LINE_MODE
    assert len(payload["lines"]) == 2


def test_real_sage_mode_allows_multiple_selected_lines(tmp_path):
    lines = []
    for index in range(2):
        line = InvoiceLine(
            ref=f"LA55-{index}",
            sage_code="RO",
            description=f"LA55-{index}",
            quantity_pieces=10,
            unit_price_ht=Decimal("6.50"),
            product_id=index + 1,
        )
        line.validate()
        lines.append(line)

    settings = AppSettings()
    settings.sage_profile.injection_mode = REAL_SAGE_ONE_LINE_MODE

    queue_path = write_injection_queue(lines, settings, tmp_path / "queue.json", line_limit=0)
    payload = json.loads(queue_path.read_text(encoding="utf-8"))

    assert payload["profile"]["injection_mode"] == REAL_SAGE_ONE_LINE_MODE
    assert len(payload["lines"]) == 2


def test_load_settings_migrates_legacy_injection_modes(tmp_path):
    settings = AppSettings()
    settings.sage_profile.injection_mode = "calibrated_clicks"
    settings.sage_profile.window_title_contains = "Sage"
    settings.sage_profile.step_mode = True
    settings.injection_line_limit = 1
    path = tmp_path / "settings.json"

    save_settings(settings, path)
    loaded = load_settings(path)

    assert loaded.sage_profile.injection_mode == REAL_SAGE_ONE_LINE_MODE
    assert loaded.sage_profile.window_title_contains == SAGE_50_WINDOW_TITLE
    assert loaded.sage_profile.step_mode is False
    assert loaded.injection_line_limit == 0


def test_settings_persist_separate_portal_credentials(tmp_path):
    settings = AppSettings()
    settings.efashion_email = "efashion@example.com"
    settings.efashion_password = "efashion-secret"
    settings.pfs_email = "pfs@example.com"
    settings.pfs_password = "pfs-secret"
    settings.portal_order_limit = 250
    path = tmp_path / "settings.json"

    save_settings(settings, path)
    loaded = load_settings(path)

    assert loaded.efashion_email == "efashion@example.com"
    assert loaded.efashion_password == "efashion-secret"
    assert loaded.pfs_email == "pfs@example.com"
    assert loaded.pfs_password == "pfs-secret"
    assert loaded.portal_order_limit == 250


def test_injection_confirmation_mode_and_stable_pause_are_persisted(tmp_path):
    settings = AppSettings()
    settings.sage_profile.confirmation_mode = "direct"
    settings.sage_profile.stable_pause_ms = 350
    settings.sage_profile.capture_before_after = False
    settings.sage_profile.log_enabled = False
    path = tmp_path / "settings.json"

    save_settings(settings, path)
    loaded = load_settings(path)

    assert loaded.sage_profile.confirmation_mode == "direct"
    assert loaded.sage_profile.stable_pause_ms == 350
    assert loaded.sage_profile.capture_before_after is False
    assert loaded.sage_profile.log_enabled is False


def test_invalid_confirmation_mode_falls_back_to_simple(tmp_path):
    settings = AppSettings()
    settings.sage_profile.confirmation_mode = "old"
    path = tmp_path / "settings.json"

    save_settings(settings, path)
    loaded = load_settings(path)

    assert loaded.sage_profile.confirmation_mode == "simple"
