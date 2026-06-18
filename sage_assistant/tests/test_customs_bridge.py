from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

from app import main as main_module
from app.main import CustomsBridgeHandler, CustomsBridgeState, MainWindow


def test_customs_bridge_returns_latest_payload():
    payload = {"parcelName": "Colis 1", "items": [{"description": "Robe"}]}
    with CustomsBridgeState.lock:
        CustomsBridgeState.payload = payload
    server = ThreadingHTTPServer(("127.0.0.1", 0), CustomsBridgeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with urlopen(f"http://{host}:{port}/latest", timeout=2) as response:
            body = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        with CustomsBridgeState.lock:
            CustomsBridgeState.payload = None

    assert body == {"ok": True, "payload": payload}


def test_publish_customs_parcel_does_not_open_browser(monkeypatch):
    class FakeMainWindow:
        def __init__(self):
            self.ensure_called = False
            self.status_messages = []

        def _ensure_customs_bridge(self):
            self.ensure_called = True

        def statusBar(self):
            return self

        def showMessage(self, message, timeout):
            self.status_messages.append((message, timeout))

    def fail_open_url(_url):
        raise AssertionError("Envoyer vers La Poste must not open a browser or tab")

    monkeypatch.setattr(main_module.QDesktopServices, "openUrl", fail_open_url)
    monkeypatch.setattr(main_module, "utc_now_iso", lambda: "2026-06-18T12:00:00Z")

    with CustomsBridgeState.lock:
        CustomsBridgeState.payload = None

    window = FakeMainWindow()
    try:
        MainWindow._publish_customs_parcel(window, {"parcelName": "Colis 1", "items": []})
        with CustomsBridgeState.lock:
            payload = CustomsBridgeState.payload
    finally:
        with CustomsBridgeState.lock:
            CustomsBridgeState.payload = None

    assert window.ensure_called is True
    assert payload == {"parcelName": "Colis 1", "items": [], "publishedAt": "2026-06-18T12:00:00Z"}
    assert window.status_messages
    assert "onglet La Poste deja ouvert" in window.status_messages[0][0]


def test_customs_dialog_buttons_keep_explicit_open_laposte_only():
    source = (main_module.Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")

    assert "Ouvrir La Poste" in source
    assert "Copier userscript" not in source
    assert "_copy_userscript" not in source
    assert "Copier URL La Poste" not in source
    assert "Copier script colis" not in source
    assert "_copy_script" not in source


def test_packing_assistant_ui_controls_are_present():
    source = (main_module.Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")

    assert "Assistant colisage" in source
    assert "packing_callback" in source
    assert "packing_button.setVisible(self.packing_callback is not None and self.source not in CUSTOMS_DISABLED_SOURCES)" in source
    assert "Importer PDF Sage" in source
    assert "_import_sage_pdf_invoice" in source
    assert "Nombre de colis" in source
    assert "Rechercher ref ou description" in source
    assert "Colis précédent" in source
    assert "Colis suivant" in source
    assert "Finir ce colis" in source
    assert "Passer cette ref" in source
    assert "Valider" in source
    assert "Appliquer" in source
    assert "Reset colisage" in source
    assert "Exporter liste de colisage" in source
    assert "Reste global" in source
    assert "_mark_dirty" in source
    assert "save_callback(self.draft)" in source
    assert "set_parcel_line_quantity" in source
    assert "QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed" in source
