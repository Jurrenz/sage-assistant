from __future__ import annotations

import os

from openpyxl import Workbook

from app.product_folder import latest_product_export, list_product_exports


def _write_product_export(path, ref: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Note Microstore"])
    sheet.append(["Référence", "Catégorie", "Colisage", "Prix"])
    sheet.append([ref, "ROBES COURTES", 12, 6.5])
    workbook.save(path)


def test_product_exports_match_ms_import_architecture(tmp_path):
    active_old = tmp_path / "MS_IMPORT" / "2026-05-04" / "Modèle d'article-1777908674360702.xlsx"
    active_latest = tmp_path / "MS_IMPORT" / "2026-05-29" / "Modèle d'article-1780039390367804.xlsx"
    disabled_latest = tmp_path / "MS_IMPORT_DISABLED" / "2026-05-29" / "Modèle d'article-1780039432367805.xlsx"
    unrelated = tmp_path / "MS_IMPORT" / "2026-05-29" / "stock_vendeur_465.xlsx"

    _write_product_export(active_old, "OLD-1")
    _write_product_export(active_latest, "NEW-1")
    _write_product_export(disabled_latest, "DISABLED-1")
    _write_product_export(unrelated, "STOCK-1")

    os.utime(active_old, (1000, 1000))
    os.utime(active_latest, (2000, 2000))
    os.utime(disabled_latest, (3000, 3000))
    os.utime(unrelated, (4000, 4000))

    exports = list_product_exports(tmp_path / "MS_IMPORT")

    assert [export.path.name for export in exports] == [
        "Modèle d'article-1780039390367804.xlsx",
        "Modèle d'article-1777908674360702.xlsx",
    ]
    assert exports[0].product_count == 1
    assert latest_product_export(tmp_path / "MS_IMPORT").path == active_latest


def test_product_exports_ignore_disabled_even_from_root(tmp_path):
    active = tmp_path / "MS_IMPORT" / "2026-05-29" / "Modèle d'article-1780039390367804.xlsx"
    disabled = tmp_path / "MS_IMPORT_DISABLED" / "2026-05-29" / "Modèle d'article-1780039432367805.xlsx"
    _write_product_export(active, "ACTIVE-1")
    _write_product_export(disabled, "DISABLED-1")
    os.utime(active, (1000, 1000))
    os.utime(disabled, (2000, 2000))

    latest = latest_product_export(tmp_path)

    assert latest is not None
    assert latest.path == active
