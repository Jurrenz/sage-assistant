from __future__ import annotations

import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from .models import Product, SageMapping, utc_now_iso
from .settings import default_db_path


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT NOT NULL UNIQUE,
    type_label TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    unit_price_ht TEXT,
    package_size INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    last_imported_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_products_ref ON products(ref);
CREATE INDEX IF NOT EXISTS idx_products_type ON products(type_label);

CREATE TABLE IF NOT EXISTS sage_type_mappings (
    microstore_type TEXT PRIMARY KEY,
    sage_code TEXT NOT NULL,
    sage_label TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


def _decimal_or_none(value: str | None) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


class Database:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def log(self, event_type: str, message: str) -> None:
        self.conn.execute(
            "INSERT INTO activity_log(created_at, event_type, message) VALUES (?, ?, ?)",
            (utc_now_iso(), event_type, message),
        )
        self.conn.commit()

    def upsert_products(self, products: Iterable[Product]) -> int:
        imported_at = utc_now_iso()
        count = 0
        with self.conn:
            for product in products:
                self.conn.execute(
                    """
                    INSERT INTO products(ref, type_label, name, unit_price_ht, package_size, active, last_imported_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(ref) DO UPDATE SET
                        type_label = excluded.type_label,
                        name = excluded.name,
                        unit_price_ht = excluded.unit_price_ht,
                        package_size = excluded.package_size,
                        active = 1,
                        last_imported_at = excluded.last_imported_at
                    """,
                    (
                        product.ref.strip().upper(),
                        product.type_label.strip(),
                        product.name.strip(),
                        str(product.unit_price_ht) if product.unit_price_ht is not None else None,
                        product.package_size,
                        imported_at,
                    ),
                )
                count += 1
        self.log("product_import", f"{count} produits importes")
        return count

    def search_products(self, text: str, limit: int = 20) -> list[Product]:
        query = text.strip().upper()
        if not query:
            return []
        rows = self.conn.execute(
            """
            SELECT * FROM products
            WHERE active = 1 AND (ref LIKE ? OR UPPER(name) LIKE ?)
            ORDER BY
                CASE WHEN ref = ? THEN 0 WHEN ref LIKE ? THEN 1 ELSE 2 END,
                ref
            LIMIT ?
            """,
            (f"%{query}%", f"%{query}%", query, f"{query}%", limit),
        ).fetchall()
        return [self._row_to_product(row) for row in rows]

    def get_product_by_ref(self, ref: str) -> Product | None:
        row = self.conn.execute(
            "SELECT * FROM products WHERE active = 1 AND ref = ?",
            (ref.strip().upper(),),
        ).fetchone()
        return self._row_to_product(row) if row else None

    def list_types_without_mapping(self) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT p.type_label
            FROM products p
            LEFT JOIN sage_type_mappings m ON m.microstore_type = p.type_label
            WHERE p.type_label <> '' AND m.microstore_type IS NULL
            ORDER BY p.type_label
            """
        ).fetchall()
        return [row["type_label"] for row in rows]

    def list_mappings(self) -> list[SageMapping]:
        rows = self.conn.execute(
            "SELECT * FROM sage_type_mappings ORDER BY microstore_type"
        ).fetchall()
        return [self._row_to_mapping(row) for row in rows]

    def get_mapping(self, microstore_type: str) -> SageMapping | None:
        row = self.conn.execute(
            "SELECT * FROM sage_type_mappings WHERE microstore_type = ? AND is_active = 1",
            (microstore_type,),
        ).fetchone()
        return self._row_to_mapping(row) if row else None

    def upsert_mapping(self, mapping: SageMapping) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO sage_type_mappings(microstore_type, sage_code, sage_label, is_active)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(microstore_type) DO UPDATE SET
                    sage_code = excluded.sage_code,
                    sage_label = excluded.sage_label,
                    is_active = excluded.is_active
                """,
                (
                    mapping.microstore_type.strip(),
                    mapping.sage_code.strip().upper(),
                    mapping.sage_label.strip(),
                    1 if mapping.is_active else 0,
                ),
            )
        self.log("mapping_update", f"Mapping mis a jour: {mapping.microstore_type}")

    def latest_product_import(self) -> str | None:
        row = self.conn.execute("SELECT MAX(last_imported_at) AS latest FROM products").fetchone()
        return row["latest"] if row else None

    def _row_to_product(self, row: sqlite3.Row) -> Product:
        return Product(
            id=row["id"],
            ref=row["ref"],
            type_label=row["type_label"],
            name=row["name"],
            unit_price_ht=_decimal_or_none(row["unit_price_ht"]),
            package_size=row["package_size"],
            active=bool(row["active"]),
            last_imported_at=row["last_imported_at"],
        )

    def _row_to_mapping(self, row: sqlite3.Row) -> SageMapping:
        return SageMapping(
            microstore_type=row["microstore_type"],
            sage_code=row["sage_code"],
            sage_label=row["sage_label"],
            is_active=bool(row["is_active"]),
        )
