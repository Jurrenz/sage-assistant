from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.db import Database
from app.models import utc_now_iso


@dataclass(frozen=True)
class WarehouseStockRecord:
    ref: str
    tail_pieces: int | None
    pieces_per_box: int | None
    box_count: int | None
    display_text: str
    total_pieces: int
    total_packages: int | None
    source_row: int
    last_synced_at: str
    notes: str = ""


@dataclass(frozen=True)
class WarehouseImportSummary:
    imported: int
    ignored: int
    ignored_refs: tuple[str, ...] = ()


class WarehouseRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def product_exists(self, ref: str) -> bool:
        return self.db.get_product_by_ref(ref) is not None

    def package_size_for_ref(self, ref: str) -> int | None:
        product = self.db.get_product_by_ref(ref)
        return product.package_size if product else None

    def get_stock(self, ref: str) -> WarehouseStockRecord | None:
        row = self.db.conn.execute(
            "SELECT * FROM warehouse_stock WHERE ref = ?",
            (ref.strip().upper(),),
        ).fetchone()
        if row is None:
            return None
        return WarehouseStockRecord(
            ref=row["ref"],
            tail_pieces=row["tail_pieces"],
            pieces_per_box=row["pieces_per_box"],
            box_count=row["box_count"],
            display_text=row["display_text"],
            total_pieces=row["total_pieces"],
            total_packages=row["total_packages"],
            source_row=row["source_row"],
            last_synced_at=row["last_synced_at"],
            notes=row["notes"],
        )

    def count_stock_rows(self) -> int:
        row = self.db.conn.execute("SELECT COUNT(*) AS count FROM warehouse_stock").fetchone()
        return int(row["count"] or 0)

    def latest_stock_sync(self) -> str | None:
        row = self.db.conn.execute("SELECT MAX(last_synced_at) AS latest FROM warehouse_stock").fetchone()
        return row["latest"] if row and row["latest"] else None

    def replace_from_import(self, records: Iterable[WarehouseStockRecord], ignored_refs: Iterable[str]) -> WarehouseImportSummary:
        imported = 0
        changed_at = utc_now_iso()
        ignored_tuple = tuple(sorted(set(ignored_refs)))
        with self.db.conn:
            for record in records:
                existing = self.db.conn.execute(
                    "SELECT * FROM warehouse_stock WHERE ref = ?",
                    (record.ref,),
                ).fetchone()
                self.db.conn.execute(
                    """
                    INSERT INTO warehouse_stock(
                        ref, tail_pieces, pieces_per_box, box_count, display_text,
                        total_pieces, total_packages, source_row, last_synced_at, notes
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ref) DO UPDATE SET
                        tail_pieces = excluded.tail_pieces,
                        pieces_per_box = excluded.pieces_per_box,
                        box_count = excluded.box_count,
                        display_text = excluded.display_text,
                        total_pieces = excluded.total_pieces,
                        total_packages = excluded.total_packages,
                        source_row = excluded.source_row,
                        last_synced_at = excluded.last_synced_at,
                        notes = excluded.notes
                    """,
                    (
                        record.ref,
                        record.tail_pieces,
                        record.pieces_per_box,
                        record.box_count,
                        record.display_text,
                        record.total_pieces,
                        record.total_packages,
                        record.source_row,
                        record.last_synced_at,
                        record.notes,
                    ),
                )
                if self._history_needed(existing, record):
                    self.db.conn.execute(
                        """
                        INSERT INTO warehouse_stock_history(
                            ref,
                            old_tail_pieces, old_pieces_per_box, old_box_count, old_total_pieces,
                            new_tail_pieces, new_pieces_per_box, new_box_count, new_total_pieces,
                            changed_at, source, note
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'xlsx', ?)
                        """,
                        (
                            record.ref,
                            existing["tail_pieces"] if existing else None,
                            existing["pieces_per_box"] if existing else None,
                            existing["box_count"] if existing else None,
                            existing["total_pieces"] if existing else None,
                            record.tail_pieces,
                            record.pieces_per_box,
                            record.box_count,
                            record.total_pieces,
                            changed_at,
                            f"Import stock.xlsx ligne {record.source_row}",
                        ),
                    )
                imported += 1
        self.db.log("warehouse_stock_import", f"{imported} stocks importes, {len(ignored_tuple)} references ignorees")
        return WarehouseImportSummary(imported=imported, ignored=len(ignored_tuple), ignored_refs=ignored_tuple)

    @staticmethod
    def _history_needed(existing, record: WarehouseStockRecord) -> bool:
        if existing is None:
            return True
        return (
            existing["tail_pieces"] != record.tail_pieces
            or existing["pieces_per_box"] != record.pieces_per_box
            or existing["box_count"] != record.box_count
            or existing["total_pieces"] != record.total_pieces
        )
