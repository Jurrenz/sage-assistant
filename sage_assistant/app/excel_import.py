from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from .models import Product


PRODUCT_HEADER_ALIASES = {
    "ref": {
        "reference",
        "reference produit",
        "ref",
        "sku",
        "code",
        "code article",
        "modele",
        "modele article",
        "article no",
        "article",
    },
    "type_label": {
        "type",
        "categorie",
        "category",
        "classification",
        "famille",
        "type article",
        "categorie article",
    },
    "name": {
        "nom",
        "nom produit",
        "description",
        "libelle",
        "designation",
        "product name",
    },
    "unit_price_ht": {
        "prix",
        "prix ht",
        "pu ht",
        "p.u. ht",
        "prix unitaire",
        "unit price",
    },
    "package_size": {
        "colisage",
        "pcs/ctn",
        "pieces par paquet",
        "piece par paquet",
        "qte paquet",
        "pack",
        "package",
    },
}

ORDER_HEADER_ALIASES = {
    "ref": PRODUCT_HEADER_ALIASES["ref"],
    "quantity": {
        "quantite",
        "qte",
        "qty",
        "quantity",
        "nombre",
    },
    "unit_price_ht": PRODUCT_HEADER_ALIASES["unit_price_ht"],
}


@dataclass(frozen=True)
class OrderRow:
    ref: str
    quantity: int
    unit_price_ht: Decimal | None = None


@dataclass(frozen=True)
class ImportResult:
    rows: list[Product] | list[OrderRow]
    warnings: list[str]


def normalize_header(value: Any) -> str:
    text = str(value or "").strip().lower()
    replacements = {
        "é": "e",
        "è": "e",
        "ê": "e",
        "à": "a",
        "ç": "c",
        "_": " ",
        "-": " ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return " ".join(text.split())


def parse_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(Decimal(str(value).strip().replace(",", ".")))
    except (InvalidOperation, ValueError):
        return None


def _load_rows(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.active
    raw_rows = list(sheet.iter_rows(values_only=True))
    if not raw_rows:
        return [], []
    headers = [normalize_header(cell) for cell in raw_rows[0]]
    rows: list[dict[str, Any]] = []
    for raw in raw_rows[1:]:
        if not any(cell not in (None, "") for cell in raw):
            continue
        rows.append({headers[index]: value for index, value in enumerate(raw) if index < len(headers)})
    return headers, rows


def _match_columns(headers: list[str], aliases: dict[str, set[str]]) -> dict[str, str]:
    matched: dict[str, str] = {}
    for field, names in aliases.items():
        normalized_aliases = {normalize_header(name) for name in names}
        for header in headers:
            if header in normalized_aliases:
                matched[field] = header
                break
    return matched


def import_products(path: Path) -> ImportResult:
    headers, rows = _load_rows(path)
    columns = _match_columns(headers, PRODUCT_HEADER_ALIASES)
    warnings: list[str] = []
    if "ref" not in columns:
        raise ValueError("Colonne reference introuvable dans l'export produits.")

    products: list[Product] = []
    for index, row in enumerate(rows, start=2):
        ref = str(row.get(columns["ref"], "") or "").strip().upper()
        if not ref:
            warnings.append(f"Ligne {index}: reference vide ignoree")
            continue
        product = Product(
            id=None,
            ref=ref,
            type_label=str(row.get(columns.get("type_label", ""), "") or "").strip(),
            name=str(row.get(columns.get("name", ""), "") or "").strip(),
            unit_price_ht=parse_decimal(row.get(columns.get("unit_price_ht", ""))),
            package_size=parse_int(row.get(columns.get("package_size", ""))),
        )
        products.append(product)
    return ImportResult(rows=products, warnings=warnings)


def import_order(path: Path) -> ImportResult:
    headers, rows = _load_rows(path)
    columns = _match_columns(headers, ORDER_HEADER_ALIASES)
    warnings: list[str] = []
    if "ref" not in columns:
        raise ValueError("Colonne reference introuvable dans l'export commande.")
    if "quantity" not in columns:
        raise ValueError("Colonne quantite introuvable dans l'export commande.")

    order_rows: list[OrderRow] = []
    for index, row in enumerate(rows, start=2):
        ref = str(row.get(columns["ref"], "") or "").strip().upper()
        quantity = parse_int(row.get(columns["quantity"]))
        if not ref:
            warnings.append(f"Ligne {index}: reference vide ignoree")
            continue
        if not quantity or quantity <= 0:
            warnings.append(f"Ligne {index}: quantite invalide pour {ref}")
            continue
        order_rows.append(
            OrderRow(
                ref=ref,
                quantity=quantity,
                unit_price_ht=parse_decimal(row.get(columns.get("unit_price_ht", ""))),
            )
        )
    return ImportResult(rows=order_rows, warnings=warnings)
