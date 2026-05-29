from __future__ import annotations

import os
from pathlib import Path

from app.order_folder import latest_order_file, list_order_files


def test_list_order_files_sorts_by_modified_time(tmp_path):
    old_file = tmp_path / "1001000.xls"
    latest_file = tmp_path / "1001627.xls"
    ignored_temp = tmp_path / "~$1001628.xls"
    ignored_pdf = tmp_path / "1001627.pdf"
    ignored_export = tmp_path / "template_sale_doc_detail.xlsx"

    old_file.write_text("old", encoding="utf-8")
    latest_file.write_text("latest", encoding="utf-8")
    ignored_temp.write_text("temp", encoding="utf-8")
    ignored_pdf.write_text("pdf", encoding="utf-8")
    ignored_export.write_text("export", encoding="utf-8")

    os.utime(old_file, (1000, 1000))
    os.utime(latest_file, (2000, 2000))

    files = list_order_files(tmp_path)

    assert [file.path.name for file in files] == ["1001627.xls", "1001000.xls"]
    assert latest_order_file(tmp_path) == latest_file


def test_list_order_files_returns_empty_for_missing_folder(tmp_path):
    assert list_order_files(tmp_path / "missing") == []
    assert latest_order_file(tmp_path / "missing") is None
