from __future__ import annotations

import os
from decimal import Decimal

from openpyxl import Workbook

from app.order_folder import latest_order_file, list_order_files, summarize_order_file


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


def test_summarize_order_file_reads_customer_and_totals(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "order_sn",
            "date",
            "customer_company",
            "customer_name",
            "customer_zip",
            "customer_city",
            "customer_phone",
            "customer_mail",
            "customer_company_id",
            "shipping_method",
        ]
    )
    sheet.append(
        [
            "1001627",
            "29-05-2026 10:17:43",
            "sas ROMIE",
            "romie Caelle boutique",
            "34200",
            "Sete",
            "0650616033",
            "client@example.com",
            "FR16849532759",
            "EASY EXPRESS",
        ]
    )
    sheet.append(["product_reference", "Barcode", "feature", "quantity", "unit", "Unit price", "Total"])
    sheet.append(["CM55-9", "2024640002238", "MIX", 2, 12, 6.5, 0])
    sheet.append(["FL307-2", "2024640001364", "MIX", 1, 12, 4.5, 0])
    path = tmp_path / "1001627.xlsx"
    workbook.save(path)

    summary = summarize_order_file(path)

    assert summary.order_number == "1001627"
    assert summary.customer_name == "romie Caelle boutique"
    assert summary.customer_city == "Sete"
    assert summary.customer_phone == "0650616033"
    assert summary.line_count == 2
    assert summary.package_count == 3
    assert summary.piece_count == 36
    assert summary.total_amount == Decimal("210.0")
