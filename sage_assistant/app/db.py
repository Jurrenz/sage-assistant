from __future__ import annotations

import sqlite3
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from .models import Product, SageMapping, utc_now_iso
from .portal_orders import PortalOrder, PortalOrderLine, PortalOrderSummary
from .settings import default_db_path
from .default_mappings import DEFAULT_SAGE_MAPPINGS


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

CREATE TABLE IF NOT EXISTS order_statuses (
    source TEXT NOT NULL,
    order_key TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(source, order_key)
);

CREATE TABLE IF NOT EXISTS cached_orders (
    source TEXT NOT NULL,
    order_key TEXT NOT NULL,
    order_id TEXT NOT NULL,
    order_number TEXT NOT NULL,
    customer TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    source_status TEXT NOT NULL DEFAULT '',
    total_amount TEXT,
    line_count INTEGER NOT NULL DEFAULT 0,
    package_count INTEGER NOT NULL DEFAULT 0,
    piece_count INTEGER NOT NULL DEFAULT 0,
    computed_status TEXT NOT NULL DEFAULT '',
    summary_json TEXT NOT NULL DEFAULT '{}',
    detail_json TEXT NOT NULL DEFAULT '{}',
    synced_at TEXT NOT NULL,
    PRIMARY KEY(source, order_key)
);

CREATE INDEX IF NOT EXISTS idx_cached_orders_created_at ON cached_orders(created_at);
CREATE INDEX IF NOT EXISTS idx_cached_orders_source ON cached_orders(source);
"""


def _decimal_or_none(value: str | None) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Type JSON non supporte: {type(value)!r}")


def _json_loads(text: str | None) -> dict:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


class Database:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.seed_default_mappings()

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
            WHERE p.type_label <> '' AND (m.microstore_type IS NULL OR m.is_active = 0)
            ORDER BY p.type_label
            """
        ).fetchall()
        return [row["type_label"] for row in rows]

    def list_mappings(self, active_only: bool = True) -> list[SageMapping]:
        where = "WHERE is_active = 1" if active_only else ""
        rows = self.conn.execute(f"SELECT * FROM sage_type_mappings {where} ORDER BY microstore_type").fetchall()
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

    def deactivate_mapping(self, microstore_type: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE sage_type_mappings SET is_active = 0 WHERE microstore_type = ?",
                (microstore_type.strip(),),
            )
        self.log("mapping_delete", f"Mapping desactive: {microstore_type}")

    def seed_default_mappings(self) -> int:
        count = 0
        with self.conn:
            for mapping in DEFAULT_SAGE_MAPPINGS:
                cursor = self.conn.execute(
                    """
                    INSERT OR IGNORE INTO sage_type_mappings(microstore_type, sage_code, sage_label, is_active)
                    VALUES (?, ?, ?, 1)
                    """,
                    (mapping.microstore_type, mapping.sage_code, mapping.sage_label),
                )
                count += cursor.rowcount
        return count

    def restore_default_mappings(self) -> int:
        count = 0
        with self.conn:
            for mapping in DEFAULT_SAGE_MAPPINGS:
                row = self.conn.execute(
                    "SELECT is_active FROM sage_type_mappings WHERE microstore_type = ?",
                    (mapping.microstore_type,),
                ).fetchone()
                if row is None:
                    self.conn.execute(
                        """
                        INSERT INTO sage_type_mappings(microstore_type, sage_code, sage_label, is_active)
                        VALUES (?, ?, ?, 1)
                        """,
                        (mapping.microstore_type, mapping.sage_code, mapping.sage_label),
                    )
                    count += 1
                elif not bool(row["is_active"]):
                    self.conn.execute(
                        "UPDATE sage_type_mappings SET is_active = 1 WHERE microstore_type = ?",
                        (mapping.microstore_type,),
                    )
                    count += 1
        if count:
            self.log("mapping_restore_defaults", f"{count} mapping(s) par defaut restaure(s)")
        return count

    def get_order_status(self, source: str, order_key: str) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM order_statuses WHERE source = ? AND order_key = ?",
            (source.strip(), order_key.strip()),
        ).fetchone()
        return row["status"] if row else None

    def set_order_status(self, source: str, order_key: str, status: str, note: str = "") -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO order_statuses(source, order_key, status, updated_at, note)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source, order_key) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    note = excluded.note
                """,
                (source.strip(), order_key.strip(), status.strip(), utc_now_iso(), note.strip()),
            )
        self.log("order_status", f"{source.strip()} {order_key.strip()} -> {status.strip()}")

    def upsert_cached_order(self, summary: PortalOrderSummary, detail: PortalOrder | None, computed_status: str) -> None:
        key = summary.order_number or summary.order_id
        detail_payload = self._order_to_payload(detail) if detail else {}
        lines = detail.lines if detail else []
        package_count = sum(line.package_count or 0 for line in lines)
        piece_count = sum(line.quantity_pieces or 0 for line in lines)
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO cached_orders(
                    source, order_key, order_id, order_number, customer, created_at, source_status,
                    total_amount, line_count, package_count, piece_count, computed_status,
                    summary_json, detail_json, synced_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, order_key) DO UPDATE SET
                    order_id = excluded.order_id,
                    order_number = excluded.order_number,
                    customer = excluded.customer,
                    created_at = excluded.created_at,
                    source_status = excluded.source_status,
                    total_amount = excluded.total_amount,
                    line_count = excluded.line_count,
                    package_count = excluded.package_count,
                    piece_count = excluded.piece_count,
                    computed_status = excluded.computed_status,
                    summary_json = excluded.summary_json,
                    detail_json = excluded.detail_json,
                    synced_at = excluded.synced_at
                """,
                (
                    summary.source,
                    key,
                    summary.order_id,
                    summary.order_number,
                    summary.customer,
                    summary.created_at,
                    summary.status,
                    str(summary.total_amount) if summary.total_amount is not None else None,
                    len(lines),
                    package_count,
                    piece_count,
                    computed_status,
                    json.dumps(summary.raw, ensure_ascii=False, default=_json_default),
                    json.dumps(detail_payload, ensure_ascii=False, default=_json_default),
                    utc_now_iso(),
                ),
            )

    def list_cached_order_summaries(self) -> list[PortalOrderSummary]:
        rows = self.conn.execute("SELECT * FROM cached_orders ORDER BY created_at DESC, order_key DESC").fetchall()
        return [self._row_to_cached_summary(row) for row in rows]

    def list_cached_order_statuses(self) -> dict[tuple[str, str], str]:
        rows = self.conn.execute("SELECT source, order_key, computed_status FROM cached_orders").fetchall()
        return {(row["source"], row["order_key"]): row["computed_status"] for row in rows}

    def get_cached_order(self, source: str, order_key: str) -> PortalOrder | None:
        row = self.conn.execute(
            "SELECT * FROM cached_orders WHERE source = ? AND order_key = ?",
            (source.strip(), order_key.strip()),
        ).fetchone()
        return self._row_to_cached_order(row) if row else None

    def latest_cached_order_sync(self, source: str | None = None) -> str | None:
        if source:
            row = self.conn.execute("SELECT MAX(synced_at) AS latest FROM cached_orders WHERE source = ?", (source,)).fetchone()
        else:
            row = self.conn.execute("SELECT MAX(synced_at) AS latest FROM cached_orders").fetchone()
        return row["latest"] if row else None

    def count_products(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS count FROM products WHERE active = 1").fetchone()
        return int(row["count"] or 0)

    def count_cached_orders(self, source: str | None = None) -> int:
        if source:
            row = self.conn.execute("SELECT COUNT(*) AS count FROM cached_orders WHERE source = ?", (source,)).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) AS count FROM cached_orders").fetchone()
        return int(row["count"] or 0)

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

    def _row_to_cached_summary(self, row: sqlite3.Row) -> PortalOrderSummary:
        return PortalOrderSummary(
            source=row["source"],
            order_id=row["order_id"],
            order_number=row["order_number"],
            customer=row["customer"],
            created_at=row["created_at"],
            status=row["source_status"],
            total_amount=_decimal_or_none(row["total_amount"]),
            raw=_json_loads(row["summary_json"]),
        )

    def _row_to_cached_order(self, row: sqlite3.Row) -> PortalOrder:
        payload = _json_loads(row["detail_json"])
        lines = [
            PortalOrderLine(
                ref=str(line.get("ref") or ""),
                category=str(line.get("category") or ""),
                description=str(line.get("description") or ""),
                package_count=int(line.get("package_count") or 0),
                package_size=int(line["package_size"]) if line.get("package_size") not in (None, "") else None,
                quantity_pieces=int(line["quantity_pieces"]) if line.get("quantity_pieces") not in (None, "") else None,
                unit_price_ht=_decimal_or_none(line.get("unit_price_ht")),
                raw=line.get("raw") if isinstance(line.get("raw"), dict) else {},
            )
            for line in payload.get("lines", [])
            if isinstance(line, dict)
        ]
        return PortalOrder(
            source=row["source"],
            order_id=row["order_id"],
            order_number=row["order_number"],
            customer=row["customer"],
            created_at=row["created_at"],
            status=row["source_status"],
            total_amount=_decimal_or_none(row["total_amount"]),
            lines=lines,
            raw=payload.get("raw") if isinstance(payload.get("raw"), dict) else {},
        )

    def _order_to_payload(self, order: PortalOrder | None) -> dict:
        if order is None:
            return {}
        return {
            "raw": order.raw,
            "lines": [
                {
                    "ref": line.ref,
                    "category": line.category,
                    "description": line.description,
                    "package_count": line.package_count,
                    "package_size": line.package_size,
                    "quantity_pieces": line.quantity_pieces,
                    "unit_price_ht": str(line.unit_price_ht) if line.unit_price_ht is not None else None,
                    "raw": line.raw,
                }
                for line in order.lines
            ],
        }
