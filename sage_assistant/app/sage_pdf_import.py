from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .excel_import import normalize_ref


LINE_RE = re.compile(
    r"^\s*(?P<index>\d+)\s+"
    r"(?P<sage_code>[A-Z]{2,5})\s+"
    r"(?P<description>.+?)\s+"
    r"(?P<quantity>\d+(?:[,.]\d{2})?)\s+"
    r"(?P<unit_price>\d+(?:[,.]\d{2})?)\s+"
    r"(?P<total_ht>\d+(?:[,.]\d{2})?)\s+"
    r"(?P<tva>\d+)"
    r"(?:\s+\d+(?:[,.]\d{2})?)?\s*$"
)
INVOICE_NUMBER_RE = re.compile(r"Facture\s*N[°o]?\s*\n?\s*(?P<number>[A-Z]{1,5}\d+)", re.IGNORECASE)
DATE_RE = re.compile(r"Date\s*\n?\s*(?P<date>\d{2}/\d{2}/\d{4})", re.IGNORECASE)
REF_RE = re.compile(r"\b[A-Z]{1,4}\d+[A-Z0-9]*(?:-\d+)?\b")
IGNORED_CODES = {"EXP"}


@dataclass(frozen=True)
class SagePdfLine:
    index: int
    sage_code: str
    description: str
    ref: str
    quantity_pieces: int
    unit_price_ht: Decimal


@dataclass(frozen=True)
class SagePdfInvoice:
    invoice_number: str
    invoice_date: str
    lines: list[SagePdfLine]
    warnings: list[str]


def _decimal_from_pdf(value: str) -> Decimal:
    try:
        return Decimal(str(value).strip().replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return Decimal("0")


def _int_from_pdf_quantity(value: str) -> int:
    return int(_decimal_from_pdf(value))


def _extract_ref(description: str) -> str:
    matches = REF_RE.findall(description.upper())
    return normalize_ref(matches[-1]) if matches else ""


def parse_sage_pdf_text(text: str) -> SagePdfInvoice:
    warnings: list[str] = []
    invoice_match = INVOICE_NUMBER_RE.search(text)
    date_match = DATE_RE.search(text)
    lines: list[SagePdfLine] = []
    for raw_line in text.splitlines():
        match = LINE_RE.match(" ".join(raw_line.split()))
        if not match:
            continue
        sage_code = match.group("sage_code").strip().upper()
        if sage_code in IGNORED_CODES:
            continue
        description = " ".join(match.group("description").split())
        ref = _extract_ref(description)
        if not ref:
            warnings.append(f"Ligne {match.group('index')}: reference introuvable ({description})")
            continue
        quantity = _int_from_pdf_quantity(match.group("quantity"))
        unit_price = _decimal_from_pdf(match.group("unit_price"))
        if quantity <= 0:
            warnings.append(f"Ligne {match.group('index')}: quantite invalide pour {ref}")
            continue
        lines.append(
            SagePdfLine(
                index=int(match.group("index")),
                sage_code=sage_code,
                description=description,
                ref=ref,
                quantity_pieces=quantity,
                unit_price_ht=unit_price,
            )
        )
    if not lines:
        raise ValueError("Aucune ligne article Sage detectee dans le PDF.")
    return SagePdfInvoice(
        invoice_number=invoice_match.group("number").strip().upper() if invoice_match else "",
        invoice_date=date_match.group("date").strip() if date_match else "",
        lines=lines,
        warnings=warnings,
    )


def extract_sage_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Le module pypdf est manquant. Installe les dependances avec: pip install -e ."
        ) from exc
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def import_sage_pdf_invoice(path: Path) -> SagePdfInvoice:
    return parse_sage_pdf_text(extract_sage_pdf_text(path))
