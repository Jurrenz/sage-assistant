from __future__ import annotations

import sqlite3
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from .models import InvoiceLine, Product, SageMapping, utc_now_iso
from .portal_orders import PortalClient, PortalOrder, PortalOrderLine, PortalOrderSummary
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
    microstore_status TEXT NOT NULL DEFAULT '',
    content_label TEXT NOT NULL DEFAULT '',
    composition TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '',
    stock_snapshot INTEGER,
    brand TEXT NOT NULL DEFAULT '',
    year TEXT NOT NULL DEFAULT '',
    season TEXT NOT NULL DEFAULT '',
    pieces_outside_package INTEGER,
    weight_grams INTEGER,
    origin_country TEXT NOT NULL DEFAULT '',
    created_at TEXT,
    promo TEXT NOT NULL DEFAULT '',
    discount_percent TEXT,
    remark TEXT NOT NULL DEFAULT '',
    colors TEXT NOT NULL DEFAULT '',
    color_distribution_1 TEXT NOT NULL DEFAULT '',
    color_1 TEXT NOT NULL DEFAULT '',
    color_distribution_2 TEXT NOT NULL DEFAULT '',
    color_2 TEXT NOT NULL DEFAULT '',
    color_distribution_3 TEXT NOT NULL DEFAULT '',
    color_3 TEXT NOT NULL DEFAULT '',
    color_distribution_4 TEXT NOT NULL DEFAULT '',
    color_4 TEXT NOT NULL DEFAULT '',
    color_distribution_5 TEXT NOT NULL DEFAULT '',
    color_5 TEXT NOT NULL DEFAULT '',
    color_distribution_6 TEXT NOT NULL DEFAULT '',
    color_6 TEXT NOT NULL DEFAULT '',
    platform_price_ht TEXT,
    platform_promo TEXT NOT NULL DEFAULT '',
    workflow_status TEXT NOT NULL DEFAULT 'synced',
    last_seen_at TEXT,
    last_microstore_modified_at TEXT,
    last_local_modified_at TEXT,
    source_json TEXT NOT NULL DEFAULT '{}',
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

CREATE TABLE IF NOT EXISTS order_line_edits (
    source TEXT NOT NULL,
    order_key TEXT NOT NULL,
    lines_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(source, order_key)
);

CREATE TABLE IF NOT EXISTS clients (
    source TEXT NOT NULL,
    client_key TEXT NOT NULL,
    client_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    zip_code TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    vat_number TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    synced_at TEXT NOT NULL,
    PRIMARY KEY(source, client_key)
);

CREATE INDEX IF NOT EXISTS idx_clients_source ON clients(source);
CREATE INDEX IF NOT EXISTS idx_clients_name ON clients(name, company);
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
        self._migrate()
        self.conn.commit()
        self.seed_default_mappings()

    def close(self) -> None:
        self.conn.close()

    def _migrate(self) -> None:
        product_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(products)").fetchall()}
        migrations = {
            "microstore_status": "ALTER TABLE products ADD COLUMN microstore_status TEXT NOT NULL DEFAULT ''",
            "content_label": "ALTER TABLE products ADD COLUMN content_label TEXT NOT NULL DEFAULT ''",
            "composition": "ALTER TABLE products ADD COLUMN composition TEXT NOT NULL DEFAULT ''",
            "color": "ALTER TABLE products ADD COLUMN color TEXT NOT NULL DEFAULT ''",
            "stock_snapshot": "ALTER TABLE products ADD COLUMN stock_snapshot INTEGER",
            "brand": "ALTER TABLE products ADD COLUMN brand TEXT NOT NULL DEFAULT ''",
            "year": "ALTER TABLE products ADD COLUMN year TEXT NOT NULL DEFAULT ''",
            "season": "ALTER TABLE products ADD COLUMN season TEXT NOT NULL DEFAULT ''",
            "pieces_outside_package": "ALTER TABLE products ADD COLUMN pieces_outside_package INTEGER",
            "weight_grams": "ALTER TABLE products ADD COLUMN weight_grams INTEGER",
            "origin_country": "ALTER TABLE products ADD COLUMN origin_country TEXT NOT NULL DEFAULT ''",
            "created_at": "ALTER TABLE products ADD COLUMN created_at TEXT",
            "promo": "ALTER TABLE products ADD COLUMN promo TEXT NOT NULL DEFAULT ''",
            "discount_percent": "ALTER TABLE products ADD COLUMN discount_percent TEXT",
            "remark": "ALTER TABLE products ADD COLUMN remark TEXT NOT NULL DEFAULT ''",
            "colors": "ALTER TABLE products ADD COLUMN colors TEXT NOT NULL DEFAULT ''",
            "color_distribution_1": "ALTER TABLE products ADD COLUMN color_distribution_1 TEXT NOT NULL DEFAULT ''",
            "color_1": "ALTER TABLE products ADD COLUMN color_1 TEXT NOT NULL DEFAULT ''",
            "color_distribution_2": "ALTER TABLE products ADD COLUMN color_distribution_2 TEXT NOT NULL DEFAULT ''",
            "color_2": "ALTER TABLE products ADD COLUMN color_2 TEXT NOT NULL DEFAULT ''",
            "color_distribution_3": "ALTER TABLE products ADD COLUMN color_distribution_3 TEXT NOT NULL DEFAULT ''",
            "color_3": "ALTER TABLE products ADD COLUMN color_3 TEXT NOT NULL DEFAULT ''",
            "color_distribution_4": "ALTER TABLE products ADD COLUMN color_distribution_4 TEXT NOT NULL DEFAULT ''",
            "color_4": "ALTER TABLE products ADD COLUMN color_4 TEXT NOT NULL DEFAULT ''",
            "color_distribution_5": "ALTER TABLE products ADD COLUMN color_distribution_5 TEXT NOT NULL DEFAULT ''",
            "color_5": "ALTER TABLE products ADD COLUMN color_5 TEXT NOT NULL DEFAULT ''",
            "color_distribution_6": "ALTER TABLE products ADD COLUMN color_distribution_6 TEXT NOT NULL DEFAULT ''",
            "color_6": "ALTER TABLE products ADD COLUMN color_6 TEXT NOT NULL DEFAULT ''",
            "platform_price_ht": "ALTER TABLE products ADD COLUMN platform_price_ht TEXT",
            "platform_promo": "ALTER TABLE products ADD COLUMN platform_promo TEXT NOT NULL DEFAULT ''",
            "workflow_status": "ALTER TABLE products ADD COLUMN workflow_status TEXT NOT NULL DEFAULT 'synced'",
            "last_seen_at": "ALTER TABLE products ADD COLUMN last_seen_at TEXT",
            "last_microstore_modified_at": "ALTER TABLE products ADD COLUMN last_microstore_modified_at TEXT",
            "last_local_modified_at": "ALTER TABLE products ADD COLUMN last_local_modified_at TEXT",
            "source_json": "ALTER TABLE products ADD COLUMN source_json TEXT NOT NULL DEFAULT '{}'",
        }
        for column, statement in migrations.items():
            if column not in product_columns:
                self.conn.execute(statement)
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS clients (
                source TEXT NOT NULL,
                client_key TEXT NOT NULL,
                client_id TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                company TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                address TEXT NOT NULL DEFAULT '',
                zip_code TEXT NOT NULL DEFAULT '',
                city TEXT NOT NULL DEFAULT '',
                country TEXT NOT NULL DEFAULT '',
                vat_number TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL DEFAULT '{}',
                synced_at TEXT NOT NULL,
                PRIMARY KEY(source, client_key)
            );
            CREATE INDEX IF NOT EXISTS idx_clients_source ON clients(source);
            CREATE INDEX IF NOT EXISTS idx_clients_name ON clients(name, company);
            """
        )

    def log(self, event_type: str, message: str) -> None:
        self.conn.execute(
            "INSERT INTO activity_log(created_at, event_type, message) VALUES (?, ?, ?)",
            (utc_now_iso(), event_type, message),
        )
        self.conn.commit()

    def upsert_products(self, products: Iterable[Product], mark_missing: bool = False) -> int:
        imported_at = utc_now_iso()
        product_rows = list(products)
        seen_refs: list[str] = []
        count = 0
        with self.conn:
            for product in product_rows:
                ref = product.ref.strip().upper()
                seen_refs.append(ref)
                self.conn.execute(
                    """
                    INSERT INTO products(
                        ref, type_label, name, unit_price_ht, package_size, active, microstore_status,
                        content_label, composition, color, stock_snapshot,
                        brand, year, season, pieces_outside_package, weight_grams, origin_country,
                        created_at, promo, discount_percent, remark,
                        colors, color_distribution_1, color_1, color_distribution_2, color_2,
                        color_distribution_3, color_3, color_distribution_4, color_4,
                        color_distribution_5, color_5, color_distribution_6, color_6,
                        platform_price_ht, platform_promo, workflow_status,
                        last_seen_at, last_microstore_modified_at, source_json, last_imported_at
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    ON CONFLICT(ref) DO UPDATE SET
                        type_label = excluded.type_label,
                        name = excluded.name,
                        unit_price_ht = excluded.unit_price_ht,
                        package_size = excluded.package_size,
                        active = 1,
                        microstore_status = excluded.microstore_status,
                        content_label = excluded.content_label,
                        composition = excluded.composition,
                        color = excluded.color,
                        stock_snapshot = excluded.stock_snapshot,
                        brand = excluded.brand,
                        year = excluded.year,
                        season = excluded.season,
                        pieces_outside_package = excluded.pieces_outside_package,
                        weight_grams = excluded.weight_grams,
                        origin_country = excluded.origin_country,
                        created_at = excluded.created_at,
                        promo = excluded.promo,
                        discount_percent = excluded.discount_percent,
                        remark = excluded.remark,
                        colors = excluded.colors,
                        color_distribution_1 = excluded.color_distribution_1,
                        color_1 = excluded.color_1,
                        color_distribution_2 = excluded.color_distribution_2,
                        color_2 = excluded.color_2,
                        color_distribution_3 = excluded.color_distribution_3,
                        color_3 = excluded.color_3,
                        color_distribution_4 = excluded.color_distribution_4,
                        color_4 = excluded.color_4,
                        color_distribution_5 = excluded.color_distribution_5,
                        color_5 = excluded.color_5,
                        color_distribution_6 = excluded.color_distribution_6,
                        color_6 = excluded.color_6,
                        platform_price_ht = excluded.platform_price_ht,
                        platform_promo = excluded.platform_promo,
                        workflow_status = 'synced',
                        last_seen_at = excluded.last_seen_at,
                        last_microstore_modified_at = excluded.last_microstore_modified_at,
                        source_json = excluded.source_json,
                        last_imported_at = excluded.last_imported_at
                    """,
                    (
                        ref,
                        product.type_label.strip(),
                        product.name.strip(),
                        str(product.unit_price_ht) if product.unit_price_ht is not None else None,
                        product.package_size,
                        1,
                        product.microstore_status.strip(),
                        product.content_label.strip(),
                        product.composition.strip(),
                        product.color.strip(),
                        product.stock_snapshot,
                        product.brand.strip(),
                        product.year.strip(),
                        product.season.strip(),
                        product.pieces_outside_package,
                        product.weight_grams,
                        product.origin_country.strip(),
                        product.created_at,
                        product.promo.strip(),
                        str(product.discount_percent) if product.discount_percent is not None else None,
                        product.remark.strip(),
                        product.colors.strip(),
                        product.color_distribution_1.strip(),
                        product.color_1.strip(),
                        product.color_distribution_2.strip(),
                        product.color_2.strip(),
                        product.color_distribution_3.strip(),
                        product.color_3.strip(),
                        product.color_distribution_4.strip(),
                        product.color_4.strip(),
                        product.color_distribution_5.strip(),
                        product.color_5.strip(),
                        product.color_distribution_6.strip(),
                        product.color_6.strip(),
                        str(product.platform_price_ht) if product.platform_price_ht is not None else None,
                        product.platform_promo.strip(),
                        "synced",
                        imported_at,
                        product.last_microstore_modified_at or imported_at,
                        json.dumps(product.raw, ensure_ascii=False, default=_json_default),
                        imported_at,
                    ),
                )
                count += 1
            if mark_missing and seen_refs:
                placeholders = ",".join("?" for _ in seen_refs)
                self.conn.execute(
                    f"""
                    UPDATE products
                    SET
                        microstore_status = CASE
                            WHEN microstore_status IN ('active', 'disabled') THEN 'absent'
                            ELSE microstore_status
                        END,
                        workflow_status = 'historical',
                        last_imported_at = ?
                    WHERE active = 1
                        AND ref NOT IN ({placeholders})
                        AND workflow_status NOT IN ('draft', 'to_create', 'modified')
                    """,
                    (imported_at, *seen_refs),
                )
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

    def list_products(
        self,
        search: str = "",
        type_filter: str = "Tous",
        status_filter: str = "Tous",
        limit: int = 500,
    ) -> list[Product]:
        clauses = ["active = 1"]
        params: list[object] = []
        query = search.strip().upper()
        if query:
            clauses.append("(ref LIKE ? OR UPPER(name) LIKE ? OR UPPER(type_label) LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%", f"%{query}%"])
        if type_filter and type_filter != "Tous":
            clauses.append("type_label = ?")
            params.append(type_filter)
        if status_filter and status_filter != "Tous":
            if status_filter == "Actif Microstore":
                clauses.append("microstore_status = 'active' AND workflow_status <> 'draft'")
            elif status_filter == "Désactivé Microstore":
                clauses.append("microstore_status = 'disabled' AND workflow_status <> 'draft'")
            elif status_filter == "Brouillon":
                clauses.append("workflow_status = 'draft'")
            elif status_filter == "À créer":
                clauses.append("workflow_status = 'to_create'")
            elif status_filter == "Modifié localement":
                clauses.append("workflow_status = 'modified'")
            elif status_filter == "Historique local":
                clauses.append("workflow_status = 'historical'")
        rows = self.conn.execute(
            f"""
            SELECT * FROM products
            WHERE {' AND '.join(clauses)}
            ORDER BY
                CASE workflow_status
                    WHEN 'to_create' THEN 0
                    WHEN 'modified' THEN 1
                    WHEN 'draft' THEN 2
                    ELSE 3
                END,
                MAX(COALESCE(last_microstore_modified_at, ''), COALESCE(last_local_modified_at, ''), COALESCE(last_seen_at, ''), COALESCE(last_imported_at, '')) DESC,
                ref
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [self._row_to_product(row) for row in rows]

    def list_product_types(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT type_label FROM products WHERE active = 1 AND type_label <> '' ORDER BY type_label"
        ).fetchall()
        return [row["type_label"] for row in rows]

    def upsert_product_draft(self, product: Product) -> Product:
        now = utc_now_iso()
        ref = product.ref.strip().upper()
        existing = self.get_product_by_ref(ref)
        workflow_status = "modified" if existing and existing.workflow_status not in {"draft", "to_create"} else product.workflow_status
        if not existing and workflow_status == "draft":
            workflow_status = "to_create"
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO products(
                    ref, type_label, name, unit_price_ht, package_size, active, microstore_status,
                    content_label, composition, color, stock_snapshot,
                    brand, year, season, pieces_outside_package, weight_grams, origin_country,
                    created_at, promo, discount_percent, remark,
                    colors, color_distribution_1, color_1, color_distribution_2, color_2,
                    color_distribution_3, color_3, color_distribution_4, color_4,
                    color_distribution_5, color_5, color_distribution_6, color_6,
                    platform_price_ht, platform_promo, workflow_status,
                    last_seen_at, last_microstore_modified_at, last_local_modified_at, source_json, last_imported_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(ref) DO UPDATE SET
                    type_label = excluded.type_label,
                    name = excluded.name,
                    unit_price_ht = excluded.unit_price_ht,
                    package_size = excluded.package_size,
                    active = 1,
                    content_label = excluded.content_label,
                    composition = excluded.composition,
                    color = excluded.color,
                    stock_snapshot = excluded.stock_snapshot,
                    brand = excluded.brand,
                    year = excluded.year,
                    season = excluded.season,
                    pieces_outside_package = excluded.pieces_outside_package,
                    weight_grams = excluded.weight_grams,
                    origin_country = excluded.origin_country,
                    created_at = excluded.created_at,
                    promo = excluded.promo,
                    discount_percent = excluded.discount_percent,
                    remark = excluded.remark,
                    colors = excluded.colors,
                    color_distribution_1 = excluded.color_distribution_1,
                    color_1 = excluded.color_1,
                    color_distribution_2 = excluded.color_distribution_2,
                    color_2 = excluded.color_2,
                    color_distribution_3 = excluded.color_distribution_3,
                    color_3 = excluded.color_3,
                    color_distribution_4 = excluded.color_distribution_4,
                    color_4 = excluded.color_4,
                    color_distribution_5 = excluded.color_distribution_5,
                    color_5 = excluded.color_5,
                    color_distribution_6 = excluded.color_distribution_6,
                    color_6 = excluded.color_6,
                    platform_price_ht = excluded.platform_price_ht,
                    platform_promo = excluded.platform_promo,
                    workflow_status = excluded.workflow_status,
                    last_local_modified_at = excluded.last_local_modified_at,
                    last_imported_at = excluded.last_imported_at
                """,
                (
                    ref,
                    product.type_label.strip(),
                    product.name.strip(),
                    str(product.unit_price_ht) if product.unit_price_ht is not None else None,
                    product.package_size,
                    1,
                    product.microstore_status.strip(),
                    product.content_label.strip(),
                    product.composition.strip(),
                    product.color.strip(),
                    product.stock_snapshot,
                    product.brand.strip(),
                    product.year.strip(),
                    product.season.strip(),
                    product.pieces_outside_package,
                    product.weight_grams,
                    product.origin_country.strip(),
                    product.created_at,
                    product.promo.strip(),
                    str(product.discount_percent) if product.discount_percent is not None else None,
                    product.remark.strip(),
                    product.colors.strip(),
                    product.color_distribution_1.strip(),
                    product.color_1.strip(),
                    product.color_distribution_2.strip(),
                    product.color_2.strip(),
                    product.color_distribution_3.strip(),
                    product.color_3.strip(),
                    product.color_distribution_4.strip(),
                    product.color_4.strip(),
                    product.color_distribution_5.strip(),
                    product.color_5.strip(),
                    product.color_distribution_6.strip(),
                    product.color_6.strip(),
                    str(product.platform_price_ht) if product.platform_price_ht is not None else None,
                    product.platform_promo.strip(),
                    workflow_status,
                    product.last_seen_at,
                    product.last_microstore_modified_at,
                    now,
                    json.dumps(product.raw, ensure_ascii=False, default=_json_default),
                    now,
                ),
            )
        self.log("product_draft", f"Brouillon produit sauvegarde: {ref}")
        saved = self.get_product_by_ref(ref)
        if saved is None:
            raise ValueError(f"Produit introuvable apres sauvegarde: {ref}")
        return saved

    def product_change_preview(self, product: Product) -> list[str]:
        existing = self.get_product_by_ref(product.ref)
        if not existing:
            return [f"Créer {product.ref} dans Microstore (simulation)"]
        if existing.workflow_status == "to_create":
            return [
                f"Créer {existing.ref} dans Microstore (simulation)",
                f"Catégorie: {existing.type_label or 'vide'}",
                f"Prix: {existing.unit_price_ht or 'vide'}",
                f"Colisage: {existing.package_size or 'vide'}",
            ]
        if existing.workflow_status == "modified":
            return [
                f"Mettre à jour {existing.ref} dans Microstore (simulation)",
                f"Catégorie: {existing.type_label or 'vide'}",
                f"Nom: {existing.name or 'vide'}",
                f"Prix: {existing.unit_price_ht or 'vide'}",
                f"Colisage: {existing.package_size or 'vide'}",
            ]
        if existing.workflow_status == "historical":
            return [f"{existing.ref} est conservé en historique local, sans écriture Microstore prévue."]
        changes: list[str] = []
        fields = [
            ("Catégorie", existing.type_label, product.type_label),
            ("Nom", existing.name, product.name),
            ("Prix", str(existing.unit_price_ht or ""), str(product.unit_price_ht or "")),
            ("Colisage", str(existing.package_size or ""), str(product.package_size or "")),
            ("Contenu colis", existing.content_label, product.content_label),
            ("Composition", existing.composition, product.composition),
            ("Couleur", existing.color, product.color),
            ("Stock connu", str(existing.stock_snapshot or ""), str(product.stock_snapshot or "")),
            ("Remarque", existing.remark, product.remark),
            ("Prix plateformes", str(existing.platform_price_ht or ""), str(product.platform_price_ht or "")),
        ]
        for label, old, new in fields:
            if old != new:
                changes.append(f"{label}: {old or 'vide'} -> {new or 'vide'}")
        return changes or ["Aucun changement détecté"]

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
                    "SELECT sage_code, sage_label, is_active FROM sage_type_mappings WHERE microstore_type = ?",
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
                elif (
                    str(row["sage_code"]).strip().upper() == mapping.sage_code.upper()
                    and str(row["sage_label"]).strip().upper() == mapping.sage_code.upper()
                    and mapping.sage_label.strip().upper() != mapping.sage_code.upper()
                ):
                    self.conn.execute(
                        "UPDATE sage_type_mappings SET sage_label = ? WHERE microstore_type = ?",
                        (mapping.sage_label, mapping.microstore_type),
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

    def delete_cached_order(self, source: str, order_key: str) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM cached_orders WHERE source = ? AND order_key = ?",
                (source.strip(), order_key.strip()),
            )
            self.conn.execute(
                "DELETE FROM order_statuses WHERE source = ? AND order_key = ?",
                (source.strip(), order_key.strip()),
            )
            self.conn.execute(
                "DELETE FROM order_line_edits WHERE source = ? AND order_key = ?",
                (source.strip(), order_key.strip()),
            )
        self.log("order_delete", f"{source.strip()} {order_key.strip()} supprime")

    def clear_cached_orders(self, source: str | None = None) -> int:
        if source:
            count = self.count_cached_orders(source)
            with self.conn:
                self.conn.execute("DELETE FROM cached_orders WHERE source = ?", (source,))
                self.conn.execute("DELETE FROM order_statuses WHERE source = ?", (source,))
                self.conn.execute("DELETE FROM order_line_edits WHERE source = ?", (source,))
            self.log("orders_clear", f"{count} commande(s) {source} supprimee(s)")
            return count
        count = self.count_cached_orders()
        with self.conn:
            self.conn.execute("DELETE FROM cached_orders")
            self.conn.execute("DELETE FROM order_statuses")
            self.conn.execute("DELETE FROM order_line_edits")
        self.log("orders_clear", f"{count} commande(s) supprimee(s)")
        return count

    def save_order_line_edits(self, source: str, order_key: str, lines: list[InvoiceLine], status: str) -> None:
        source_key = source.strip()
        order_key_clean = order_key.strip()
        updated_at = utc_now_iso()
        payload = [self._invoice_line_to_payload(line) for line in lines]
        package_count = sum(line.package_count or 0 for line in lines)
        piece_count = sum(line.quantity_pieces or 0 for line in lines)
        total_amount = sum((line.unit_price_ht or Decimal("0")) * Decimal(line.quantity_pieces or 0) for line in lines)
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO order_line_edits(source, order_key, lines_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source, order_key) DO UPDATE SET
                    lines_json = excluded.lines_json,
                    updated_at = excluded.updated_at
                """,
                (source_key, order_key_clean, json.dumps(payload, ensure_ascii=False, default=_json_default), updated_at),
            )
            self.conn.execute(
                """
                UPDATE cached_orders
                SET
                    computed_status = ?,
                    line_count = ?,
                    package_count = ?,
                    piece_count = ?,
                    total_amount = ?
                WHERE source = ? AND order_key = ?
                """,
                (status, len(lines), package_count, piece_count, str(total_amount), source_key, order_key_clean),
            )
            self.conn.execute(
                """
                INSERT INTO order_statuses(source, order_key, status, updated_at, note)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source, order_key) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    note = excluded.note
                """,
                (source_key, order_key_clean, status, updated_at, "corrections auto"),
            )
        self.log("order_line_edits", f"{source_key} {order_key_clean}: {len(lines)} ligne(s) sauvegardee(s)")

    def get_order_line_edits(self, source: str, order_key: str) -> list[InvoiceLine] | None:
        row = self.conn.execute(
            "SELECT lines_json FROM order_line_edits WHERE source = ? AND order_key = ?",
            (source.strip(), order_key.strip()),
        ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row["lines_json"])
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, list):
            return None
        return [self._invoice_line_from_payload(item) for item in payload if isinstance(item, dict)]

    def clear_order_line_edits(self, source: str, order_key: str) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM order_line_edits WHERE source = ? AND order_key = ?",
                (source.strip(), order_key.strip()),
            )
        self.log("order_line_edits_reset", f"{source.strip()} {order_key.strip()}")

    def upsert_clients(self, clients: Iterable[PortalClient]) -> int:
        rows = list(clients)
        synced_at = utc_now_iso()
        count = 0
        with self.conn:
            for client in rows:
                key = (client.client_key or client.client_id or client.email or client.company or client.name).strip()
                if not key:
                    continue
                self.conn.execute(
                    """
                    INSERT INTO clients(
                        source, client_key, client_id, name, company, phone, email,
                        address, zip_code, city, country, vat_number, raw_json, synced_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, client_key) DO UPDATE SET
                        client_id = excluded.client_id,
                        name = excluded.name,
                        company = excluded.company,
                        phone = excluded.phone,
                        email = excluded.email,
                        address = excluded.address,
                        zip_code = excluded.zip_code,
                        city = excluded.city,
                        country = excluded.country,
                        vat_number = excluded.vat_number,
                        raw_json = excluded.raw_json,
                        synced_at = excluded.synced_at
                    """,
                    (
                        client.source,
                        key,
                        client.client_id,
                        client.name,
                        client.company,
                        client.phone,
                        client.email,
                        client.address,
                        client.zip_code,
                        client.city,
                        client.country,
                        client.vat_number,
                        json.dumps(client.raw, ensure_ascii=False, default=_json_default),
                        synced_at,
                    ),
                )
                count += 1
        if count:
            self.log("client_sync", f"{count} client(s) sauvegarde(s)")
        return count

    def list_clients(self, source: str = "Microstore", search: str = "", limit: int = 1000) -> list[PortalClient]:
        clauses = ["source = ?"]
        params: list[object] = [source]
        query = search.strip().lower()
        if query:
            clauses.append(
                "(LOWER(name) LIKE ? OR LOWER(company) LIKE ? OR LOWER(email) LIKE ? OR LOWER(phone) LIKE ? OR LOWER(city) LIKE ?)"
            )
            params.extend([f"%{query}%"] * 5)
        rows = self.conn.execute(
            f"""
            SELECT * FROM clients
            WHERE {' AND '.join(clauses)}
            ORDER BY synced_at DESC, company COLLATE NOCASE, name COLLATE NOCASE
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [self._row_to_client(row) for row in rows]

    def get_client(self, source: str, client_key: str) -> PortalClient | None:
        row = self.conn.execute(
            "SELECT * FROM clients WHERE source = ? AND client_key = ?",
            (source.strip(), client_key.strip()),
        ).fetchone()
        return self._row_to_client(row) if row else None

    def find_client(self, source: str, text: str) -> PortalClient | None:
        query = text.strip().lower()
        if not query:
            return None
        rows = self.list_clients(source=source, search=query, limit=5)
        return rows[0] if rows else None

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

    def latest_client_sync(self, source: str | None = None) -> str | None:
        if source:
            row = self.conn.execute("SELECT MAX(synced_at) AS latest FROM clients WHERE source = ?", (source,)).fetchone()
        else:
            row = self.conn.execute("SELECT MAX(synced_at) AS latest FROM clients").fetchone()
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

    def count_clients(self, source: str | None = None) -> int:
        if source:
            row = self.conn.execute("SELECT COUNT(*) AS count FROM clients WHERE source = ?", (source,)).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) AS count FROM clients").fetchone()
        return int(row["count"] or 0)

    def latest_product_import(self) -> str | None:
        row = self.conn.execute("SELECT MAX(last_imported_at) AS latest FROM products").fetchone()
        return row["latest"] if row else None

    def count_products_by_microstore_status(self, status: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS count FROM products WHERE active = 1 AND microstore_status = ?",
            (status,),
        ).fetchone()
        return int(row["count"] or 0)

    def count_products_by_workflow_status(self, status: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS count FROM products WHERE active = 1 AND workflow_status = ?",
            (status,),
        ).fetchone()
        return int(row["count"] or 0)

    def _row_to_product(self, row: sqlite3.Row) -> Product:
        return Product(
            id=row["id"],
            ref=row["ref"],
            type_label=row["type_label"],
            name=row["name"],
            unit_price_ht=_decimal_or_none(row["unit_price_ht"]),
            package_size=row["package_size"],
            active=bool(row["active"]),
            microstore_status=row["microstore_status"] if "microstore_status" in row.keys() else "",
            content_label=row["content_label"] if "content_label" in row.keys() else "",
            composition=row["composition"] if "composition" in row.keys() else "",
            color=row["color"] if "color" in row.keys() else "",
            stock_snapshot=row["stock_snapshot"] if "stock_snapshot" in row.keys() else None,
            brand=row["brand"] if "brand" in row.keys() else "",
            year=row["year"] if "year" in row.keys() else "",
            season=row["season"] if "season" in row.keys() else "",
            pieces_outside_package=row["pieces_outside_package"] if "pieces_outside_package" in row.keys() else None,
            weight_grams=row["weight_grams"] if "weight_grams" in row.keys() else None,
            origin_country=row["origin_country"] if "origin_country" in row.keys() else "",
            created_at=row["created_at"] if "created_at" in row.keys() else None,
            promo=row["promo"] if "promo" in row.keys() else "",
            discount_percent=_decimal_or_none(row["discount_percent"]) if "discount_percent" in row.keys() else None,
            remark=row["remark"] if "remark" in row.keys() else "",
            colors=row["colors"] if "colors" in row.keys() else "",
            color_distribution_1=row["color_distribution_1"] if "color_distribution_1" in row.keys() else "",
            color_1=row["color_1"] if "color_1" in row.keys() else "",
            color_distribution_2=row["color_distribution_2"] if "color_distribution_2" in row.keys() else "",
            color_2=row["color_2"] if "color_2" in row.keys() else "",
            color_distribution_3=row["color_distribution_3"] if "color_distribution_3" in row.keys() else "",
            color_3=row["color_3"] if "color_3" in row.keys() else "",
            color_distribution_4=row["color_distribution_4"] if "color_distribution_4" in row.keys() else "",
            color_4=row["color_4"] if "color_4" in row.keys() else "",
            color_distribution_5=row["color_distribution_5"] if "color_distribution_5" in row.keys() else "",
            color_5=row["color_5"] if "color_5" in row.keys() else "",
            color_distribution_6=row["color_distribution_6"] if "color_distribution_6" in row.keys() else "",
            color_6=row["color_6"] if "color_6" in row.keys() else "",
            platform_price_ht=_decimal_or_none(row["platform_price_ht"]) if "platform_price_ht" in row.keys() else None,
            platform_promo=row["platform_promo"] if "platform_promo" in row.keys() else "",
            workflow_status=row["workflow_status"] if "workflow_status" in row.keys() else "synced",
            last_seen_at=row["last_seen_at"] if "last_seen_at" in row.keys() else None,
            last_microstore_modified_at=row["last_microstore_modified_at"] if "last_microstore_modified_at" in row.keys() else None,
            last_local_modified_at=row["last_local_modified_at"] if "last_local_modified_at" in row.keys() else None,
            raw=_json_loads(row["source_json"]) if "source_json" in row.keys() else {},
            last_imported_at=row["last_imported_at"],
        )

    def _row_to_mapping(self, row: sqlite3.Row) -> SageMapping:
        return SageMapping(
            microstore_type=row["microstore_type"],
            sage_code=row["sage_code"],
            sage_label=row["sage_label"],
            is_active=bool(row["is_active"]),
        )

    def _row_to_client(self, row: sqlite3.Row) -> PortalClient:
        return PortalClient(
            source=row["source"],
            client_id=row["client_id"],
            client_key=row["client_key"],
            name=row["name"],
            company=row["company"],
            phone=row["phone"],
            email=row["email"],
            address=row["address"],
            zip_code=row["zip_code"],
            city=row["city"],
            country=row["country"],
            vat_number=row["vat_number"],
            raw=_json_loads(row["raw_json"]),
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

    def _invoice_line_to_payload(self, line: InvoiceLine) -> dict:
        return {
            "ref": line.ref,
            "sage_code": line.sage_code,
            "description": line.description,
            "quantity_pieces": line.quantity_pieces,
            "package_count": line.package_count,
            "package_size": line.package_size,
            "unit_price_ht": str(line.unit_price_ht) if line.unit_price_ht is not None else None,
            "catalog_unit_price_ht": str(line.catalog_unit_price_ht) if line.catalog_unit_price_ht is not None else None,
            "order_unit_price_ht": str(line.order_unit_price_ht) if line.order_unit_price_ht is not None else None,
            "price_confirmed": line.price_confirmed,
            "product_id": line.product_id,
            "type_label": line.type_label,
            "validation_status": line.validation_status,
            "validation_message": line.validation_message,
            "source": line.source,
        }

    def _invoice_line_from_payload(self, payload: dict) -> InvoiceLine:
        line = InvoiceLine(
            ref=str(payload.get("ref") or "").strip().upper(),
            sage_code=str(payload.get("sage_code") or ""),
            description=str(payload.get("description") or ""),
            quantity_pieces=int(payload.get("quantity_pieces") or 0),
            package_count=int(payload["package_count"]) if payload.get("package_count") not in (None, "") else None,
            package_size=int(payload["package_size"]) if payload.get("package_size") not in (None, "") else None,
            unit_price_ht=_decimal_or_none(payload.get("unit_price_ht")),
            catalog_unit_price_ht=_decimal_or_none(payload.get("catalog_unit_price_ht")),
            order_unit_price_ht=_decimal_or_none(payload.get("order_unit_price_ht")),
            price_confirmed=bool(payload.get("price_confirmed", True)),
            product_id=int(payload["product_id"]) if payload.get("product_id") not in (None, "") else None,
            type_label=str(payload.get("type_label") or ""),
            validation_status=str(payload.get("validation_status") or "pending"),
            validation_message=str(payload.get("validation_message") or ""),
            source=str(payload.get("source") or "manual"),
        )
        line.validate()
        return line

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
