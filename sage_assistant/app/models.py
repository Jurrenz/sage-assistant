from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal


def normalize_spaces(value: str) -> str:
    return " ".join(str(value or "").split())


def build_sage_description(ref: str, sage_label: str = "", fallback: str = "") -> str:
    label = normalize_spaces(sage_label or fallback)
    ref_text = normalize_spaces(ref)
    if label and ref_text and ref_text not in label.split():
        return normalize_spaces(f"{label} {ref_text}")
    return normalize_spaces(label or ref_text)


@dataclass(frozen=True)
class Product:
    id: int | None
    ref: str
    type_label: str
    name: str
    unit_price_ht: Decimal | None
    package_size: int | None
    active: bool = True
    microstore_status: str = ""
    content_label: str = ""
    composition: str = ""
    color: str = ""
    stock_snapshot: int | None = None
    brand: str = ""
    year: str = ""
    season: str = ""
    pieces_outside_package: int | None = None
    weight_grams: int | None = None
    origin_country: str = ""
    created_at: str | None = None
    promo: str = ""
    discount_percent: Decimal | None = None
    remark: str = ""
    colors: str = ""
    color_distribution_1: str = ""
    color_1: str = ""
    color_distribution_2: str = ""
    color_2: str = ""
    color_distribution_3: str = ""
    color_3: str = ""
    color_distribution_4: str = ""
    color_4: str = ""
    color_distribution_5: str = ""
    color_5: str = ""
    color_distribution_6: str = ""
    color_6: str = ""
    platform_price_ht: Decimal | None = None
    platform_promo: str = ""
    workflow_status: str = "synced"
    last_seen_at: str | None = None
    last_microstore_modified_at: str | None = None
    last_local_modified_at: str | None = None
    raw: dict = field(default_factory=dict)
    last_imported_at: str | None = None


@dataclass(frozen=True)
class SageMapping:
    microstore_type: str
    sage_code: str
    sage_label: str
    is_active: bool = True


@dataclass
class InvoiceLine:
    ref: str
    sage_code: str = ""
    description: str = ""
    quantity_pieces: int = 0
    package_count: int | None = None
    package_size: int | None = None
    unit_price_ht: Decimal | None = None
    catalog_unit_price_ht: Decimal | None = None
    order_unit_price_ht: Decimal | None = None
    price_confirmed: bool = True
    product_id: int | None = None
    type_label: str = ""
    validation_status: str = "pending"
    validation_message: str = ""
    source: str = "manual"

    def validate(self) -> None:
        errors: list[str] = []
        external_sources = {"PFS", "eFashion"}
        if not self.description and self.ref:
            self.description = self.ref
        if self.product_id is None and self.source not in external_sources:
            errors.append("reference non resolue")
        if not self.sage_code:
            errors.append("code Sage absent")
        if not self.description:
            errors.append("description absente")
        if self.quantity_pieces <= 0:
            errors.append("quantite invalide")
        if self.unit_price_ht is None:
            errors.append("prix absent")
        self.validation_status = "ok" if not errors else "blocked"
        self.validation_message = "; ".join(errors)

    def as_injection_dict(self) -> dict[str, object]:
        return {
            "article_code": self.sage_code,
            "description": normalize_spaces(self.description),
            "quantity": self.quantity_pieces,
            "unit_price_ht": str(self.unit_price_ht or ""),
            "ref": self.ref,
        }


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
