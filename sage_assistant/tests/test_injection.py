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
