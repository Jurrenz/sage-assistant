from __future__ import annotations

import json
from decimal import Decimal

from app.injection import write_injection_queue
from app.models import InvoiceLine
from app.settings import AppSettings


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


def test_write_injection_queue_can_limit_lines(tmp_path):
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
    settings.sage_profile.step_mode = True
    queue_path = write_injection_queue(lines, settings, tmp_path / "queue.json", line_limit=1)
    payload = json.loads(queue_path.read_text(encoding="utf-8"))

    assert payload["source_line_count"] == 3
    assert payload["line_limit"] == 1
    assert len(payload["lines"]) == 1
    assert payload["profile"]["step_mode"] is True
    assert payload["profile"]["focus_guard"] is True


def test_write_injection_queue_limit_ignores_blocked_lines_outside_selection(tmp_path):
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
        sage_code="RO",
        description="FL96-9",
        quantity_pieces=12,
        unit_price_ht=Decimal("4.00"),
        product_id=2,
        price_confirmed=False,
    )
    blocked_line.validate()

    queue_path = write_injection_queue([ok_line, blocked_line], AppSettings(), tmp_path / "queue.json", line_limit=1)
    payload = json.loads(queue_path.read_text(encoding="utf-8"))

    assert len(payload["lines"]) == 1
    assert payload["lines"][0]["ref"] == "CM55-9"
