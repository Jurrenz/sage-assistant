from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.db import Database
from app.main import SyncWorker, _order_web_url, should_sync_microstore_products
from app.models import Product, SageMapping
from app.portal_orders import PortalOrder, PortalOrderLine, PortalOrderSummary


class FakeMicrostoreConnector:
    product_calls = 0

    def __init__(self, _token: str) -> None:
        self.details_seen = 0

    def list_products(self) -> list[Product]:
        type(self).product_calls += 1
        return [
            Product(
                id=None,
                ref="FL530-1",
                type_label="ROBES COURTES",
                name="Robe test",
                unit_price_ht=Decimal("6.80"),
                package_size=12,
            )
        ]

    def list_orders(self, days: int) -> list[PortalOrderSummary]:
        return [
            PortalOrderSummary(source="Microstore", order_id="1", order_number="MS1", customer="Client A"),
            PortalOrderSummary(source="Microstore", order_id="2", order_number="MS2", customer="Client B"),
        ]

    def get_order(self, order_id: str) -> PortalOrder:
        self.details_seen += 1
        return PortalOrder(
            source="Microstore",
            order_id=order_id,
            order_number=f"MS{order_id}",
            customer=f"Client {order_id}",
            lines=[
                PortalOrderLine(
                    ref="FL530-1",
                    package_count=1,
                    package_size=12,
                    quantity_pieces=12,
                    unit_price_ht=Decimal("6.80"),
                )
            ],
        )


def _worker(tmp_path, monkeypatch) -> SyncWorker:
    db_path = tmp_path / "app.sqlite"
    monkeypatch.setattr("app.main.default_db_path", lambda: db_path)
    monkeypatch.setattr("app.main.MicrostoreConnector", FakeMicrostoreConnector)
    db = Database(db_path)
    db.upsert_mapping(SageMapping("ROBES COURTES", "RO", "ROBE / TUNIC"))
    db.close()
    return SyncWorker(
        ["Microstore"],
        "token",
        30,
        25,
        "",
        "",
        "",
        "",
    )


def test_sync_worker_microstore_orders_do_not_force_product_sync(tmp_path, monkeypatch):
    FakeMicrostoreConnector.product_calls = 0
    worker = _worker(tmp_path, monkeypatch)
    results = []
    progress = []
    worker.progress.connect(lambda source, percent, message: progress.append((source, percent, message)))
    worker.all_finished.connect(results.append)

    worker.run()

    db = Database(tmp_path / "app.sqlite")
    assert FakeMicrostoreConnector.product_calls == 0
    assert db.count_products() == 0
    assert db.count_cached_orders("Microstore") == 2
    assert db.get_cached_order("Microstore", "MS1") is not None
    assert results[0]["sources"]["Microstore"]["orders"] == 2
    assert progress[-1] == ("Microstore", 100, "2 commandes, 0 clients")
    db.close()


def test_sync_worker_microstore_products_persists_with_own_database_connection(tmp_path, monkeypatch):
    FakeMicrostoreConnector.product_calls = 0
    worker = _worker(tmp_path, monkeypatch)
    worker.sources = ["MicrostoreProducts"]
    results = []
    progress = []
    worker.progress.connect(lambda source, percent, message: progress.append((source, percent, message)))
    worker.all_finished.connect(results.append)

    worker.run()

    db = Database(tmp_path / "app.sqlite")
    assert FakeMicrostoreConnector.product_calls == 1
    assert db.count_products() == 1
    assert results[0]["sources"]["MicrostoreProducts"]["products"] == 1
    assert progress[-1] == ("MicrostoreProducts", 100, "1 produits sauvegardes")
    db.close()


def test_sync_worker_cancel_keeps_already_saved_orders(tmp_path, monkeypatch):
    worker = _worker(tmp_path, monkeypatch)

    def cancel_after_first_detail(_source: str, _percent: int, message: str) -> None:
        if message.startswith("details 1/"):
            worker.cancel()

    results = []
    worker.progress.connect(cancel_after_first_detail)
    worker.all_finished.connect(results.append)

    worker.run()

    db = Database(tmp_path / "app.sqlite")
    assert db.count_cached_orders("Microstore") == 1
    assert db.get_cached_order("Microstore", "MS1") is not None
    assert results[0]["sources"]["Microstore"]["cancelled"] is True
    assert results[0]["sources"]["Microstore"]["orders"] == 1
    db.close()


def test_order_web_urls_match_portal_routes():
    assert (
        _order_web_url("PFS", "ord_67e994e8a40a6ccf833b2ff6f523", "PO#42017936")
        == "https://wholesaler.parisfashionshops.com/orders/ord_67e994e8a40a6ccf833b2ff6f523/details"
    )
    assert (
        _order_web_url("eFashion", "10567871")
        == "https://wholesaler.efashion-paris.com/orderdetails/10567871?page=1&limit=25"
    )
    assert (
        _order_web_url("Microstore", "1001639", microstore_token="token")
        == "https://mc2-h5.dokkr.net/order-detail.html?doc_id=1001639&lang=fr&key=token"
    )


def test_should_sync_microstore_products_respects_resync_delay():
    recent = (datetime.now(UTC) - timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    old = (datetime.now(UTC) - timedelta(hours=8)).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    assert should_sync_microstore_products(None, 6) is True
    assert should_sync_microstore_products(recent, 6) is False
    assert should_sync_microstore_products(old, 6) is True
    assert should_sync_microstore_products(recent, 0) is True


def test_main_window_uses_ui_thread_relay_for_sync_completion():
    source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")

    assert "class SyncCompletionRelay(QObject):" in source
    assert "self.sync_completion_relay.finished.connect(self._on_sync_all_finished)" in source
    assert "self._on_sync_all_finished(result, t, src, all_sync)" not in source
