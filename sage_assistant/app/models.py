from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal


@dataclass(frozen=True)
class Product:
    id: int | None
    ref: str
    type_label: str
    name: str
    unit_price_ht: Decimal | None
    package_size: int | None
    active: bool = True
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
        if not self.price_confirmed:
            errors.append("ecart prix a confirmer")
        self.validation_status = "ok" if not errors else "blocked"
        self.validation_message = "; ".join(errors)

    def as_injection_dict(self) -> dict[str, object]:
        return {
            "article_code": self.sage_code,
            "description": self.description,
            "quantity": self.quantity_pieces,
            "unit_price_ht": str(self.unit_price_ht or ""),
            "ref": self.ref,
        }


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
