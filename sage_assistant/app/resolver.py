from __future__ import annotations

from decimal import Decimal

from .db import Database
from .excel_import import OrderRow
from .models import InvoiceLine, Product
from .portal_orders import PortalOrderLine


PORTAL_CATEGORY_ALIASES = {
    "ROBE": "ROBES COURTES",
    "ROBES": "ROBES COURTES",
    "ROBES COURTES": "ROBES COURTES",
    "ROBE COURTE": "ROBES COURTES",
    "ROBE LONGUE": "ROBES LONGUES",
    "ROBES LONGUES": "ROBES LONGUES",
    "DRESSES": "ROBES COURTES",
    "DRESS": "ROBES COURTES",
    "TOP": "TOPS",
    "TOPS": "TOPS",
    "HAUT": "TOPS",
    "HAUTS": "TOPS",
    "TUNIQUE": "TOPS",
    "TUNIQUES": "TOPS",
    "BLOUSE": "CHEMISES / TUNIQUES",
    "BLOUSES": "CHEMISES / TUNIQUES",
    "CHEMISE": "CHEMISES / TUNIQUES",
    "CHEMISES": "CHEMISES / TUNIQUES",
    "CHEMISES TUNIQUES": "CHEMISES / TUNIQUES",
    "COMBI": "COMBI PANTALON",
    "COMBINAISON": "COMBI PANTALON",
    "COMBINAISONS": "COMBI PANTALON",
    "COMBIS": "COMBI PANTALON",
    "PANTALON": "PANTALONS",
    "PANTALONS": "PANTALONS",
    "PANTS": "PANTALONS",
    "JUPE": "JUPES",
    "JUPES": "JUPES",
    "SKIRTS": "JUPES",
    "VESTE": "MANTEAUX / VESTES",
    "VESTES": "MANTEAUX / VESTES",
    "MANTEAU": "MANTEAUX / VESTES",
    "MANTEAUX": "MANTEAUX / VESTES",
    "SHORT": "SHORTS",
    "SHORTS": "SHORTS",
    "PULL": "PULLS / GILETS",
    "PULLS": "PULLS / GILETS",
    "GILET": "PULLS / GILETS",
    "GILETS": "PULLS / GILETS",
    "ENSEMBLE": "ENSEMBLES",
    "ENSEMBLES": "ENSEMBLES",
    "BEACHWEAR": "VÊTEMENTS PLAGE",
    "PLAGE": "VÊTEMENTS PLAGE",
    "CROCHET": "CROCHETS",
    "CROCHETS": "CROCHETS",
}


def _mapping_type_from_portal_category(category: str) -> str:
    normalized = " ".join(
        category.strip().upper().replace("É", "E").replace("È", "E").replace("Ê", "E").replace("À", "A").replace("/", " ").split()
    )
    exact = category.strip().upper()
    if normalized:
        if normalized in PORTAL_CATEGORY_ALIASES:
            return PORTAL_CATEGORY_ALIASES[normalized]
        for needle, mapped in PORTAL_CATEGORY_ALIASES.items():
            if needle in normalized:
                return mapped
        return exact
    return ""


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
        retained_price = unit_price_ht if unit_price_ht is not None else product.unit_price_ht
        price_confirmed = unit_price_ht is None or product.unit_price_ht is None or unit_price_ht == product.unit_price_ht
        line = InvoiceLine(
            ref=product.ref,
            sage_code=sage_code,
            description=product.ref,
            quantity_pieces=quantity_pieces,
            package_count=package_count,
            package_size=product.package_size,
            unit_price_ht=retained_price,
            catalog_unit_price_ht=product.unit_price_ht,
            order_unit_price_ht=unit_price_ht,
            price_confirmed=price_confirmed,
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
            line = InvoiceLine(ref=ref.strip().upper(), quantity_pieces=quantity_pieces, unit_price_ht=unit_price_ht, order_unit_price_ht=unit_price_ht, source=source)
            line.validate()
            return line
        return self.line_from_product(product, quantity_pieces, unit_price_ht, source=source)

    def line_from_order_row(self, row: OrderRow, source: str = "microstore_excel") -> InvoiceLine:
        product = self.db.get_product_by_ref(row.ref)
        quantity_pieces = row.quantity_pieces
        package_size = row.package_size
        if product and product.package_size:
            package_size = product.package_size
        if (quantity_pieces is None or quantity_pieces <= 0) and row.package_count > 0 and package_size:
            quantity_pieces = row.package_count * package_size
        if quantity_pieces is None or quantity_pieces <= 0:
            quantity_pieces = row.package_count
        if not product:
            line = InvoiceLine(
                ref=row.ref,
                quantity_pieces=quantity_pieces,
                package_count=row.package_count or None,
                package_size=package_size,
                unit_price_ht=row.unit_price_ht,
                order_unit_price_ht=row.unit_price_ht,
                source=source,
            )
            line.validate()
            return line
        return self.line_from_product(
            product,
            quantity_pieces=quantity_pieces,
            unit_price_ht=row.unit_price_ht,
            package_count=row.package_count or None,
            source=source,
        )

    def lines_from_order_rows(self, rows: list[OrderRow], source: str = "microstore_excel") -> list[InvoiceLine]:
        return [self.line_from_order_row(row, source=source) for row in rows]

    def line_from_portal_line(self, row: PortalOrderLine, source: str) -> InvoiceLine:
        product = self.db.get_product_by_ref(row.ref)
        quantity_pieces = row.quantity_pieces
        package_size = row.package_size
        if product and product.package_size:
            package_size = product.package_size
        if (quantity_pieces is None or quantity_pieces <= 0) and row.package_count > 0 and package_size:
            quantity_pieces = row.package_count * package_size
        if quantity_pieces is None or quantity_pieces <= 0:
            quantity_pieces = row.package_count
        if product:
            line = self.line_from_product(
                product,
                quantity_pieces=quantity_pieces,
                unit_price_ht=row.unit_price_ht,
                package_count=row.package_count or None,
                source=source,
            )
            line.description = product.ref
            line.validate()
            return line

        type_label = _mapping_type_from_portal_category(row.category)
        mapping = self.db.get_mapping(type_label) if type_label else None
        line = InvoiceLine(
            ref=row.ref,
            sage_code=mapping.sage_code if mapping else "",
            description=row.ref or row.description,
            quantity_pieces=quantity_pieces,
            package_count=row.package_count or None,
            package_size=package_size,
            unit_price_ht=row.unit_price_ht,
            order_unit_price_ht=row.unit_price_ht,
            product_id=0,
            type_label=type_label or row.category,
            source=source,
        )
        line.validate()
        return line

    def lines_from_portal_lines(self, rows: list[PortalOrderLine], source: str) -> list[InvoiceLine]:
        return [self.line_from_portal_line(row, source=source) for row in rows]
