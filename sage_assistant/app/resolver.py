from __future__ import annotations

from decimal import Decimal

from .db import Database
from .excel_import import OrderRow
from .models import InvoiceLine, Product


class Resolver:
    def __init__(self, db: Database) -> None:
        self.db = db

    def line_from_product(
        self,
        product: Product,
        quantity_pieces: int,
        unit_price_ht: Decimal | None = None,
        package_count: int | None = None,
        source: str = "manual",
    ) -> InvoiceLine:
        mapping = self.db.get_mapping(product.type_label)
        sage_code = mapping.sage_code if mapping else ""
        sage_label = mapping.sage_label if mapping else product.type_label
        line = InvoiceLine(
            ref=product.ref,
            sage_code=sage_code,
            description=f"{sage_label} {product.ref}".strip(),
            quantity_pieces=quantity_pieces,
            package_count=package_count,
            package_size=product.package_size,
            unit_price_ht=unit_price_ht if unit_price_ht is not None else product.unit_price_ht,
            product_id=product.id,
            type_label=product.type_label,
            source=source,
        )
        line.validate()
        return line

    def line_from_ref(
        self,
        ref: str,
        quantity_pieces: int,
        unit_price_ht: Decimal | None = None,
        source: str = "manual",
    ) -> InvoiceLine:
        product = self.db.get_product_by_ref(ref)
        if not product:
            line = InvoiceLine(ref=ref.strip().upper(), quantity_pieces=quantity_pieces, unit_price_ht=unit_price_ht, source=source)
            line.validate()
            return line
        return self.line_from_product(product, quantity_pieces, unit_price_ht, source=source)

    def line_from_order_row(self, row: OrderRow) -> InvoiceLine:
        return self.line_from_ref(
            ref=row.ref,
            quantity_pieces=row.quantity,
            unit_price_ht=row.unit_price_ht,
            source="microstore_excel",
        )
