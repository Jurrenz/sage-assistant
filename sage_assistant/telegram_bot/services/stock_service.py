from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.excel_import import normalize_ref
from telegram_bot.database.warehouse_repository import WarehouseRepository, WarehouseStockRecord


@dataclass(frozen=True)
class StockLookupResult:
    status: str
    ref: str
    product_found: bool
    stock: WarehouseStockRecord | None = None


class StockService:
    def __init__(self, repository: WarehouseRepository, timezone: str = "Europe/Paris") -> None:
        self.repository = repository
        try:
            self.timezone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            self.timezone = ZoneInfo("UTC")

    def lookup(self, raw_ref: str) -> StockLookupResult:
        ref = normalize_ref(raw_ref)
        if not ref or not self.repository.product_exists(ref):
            return StockLookupResult(status="missing_product", ref=ref, product_found=False)
        stock = self.repository.get_stock(ref)
        if stock is None:
            return StockLookupResult(status="missing_stock", ref=ref, product_found=True)
        return StockLookupResult(status="ok", ref=ref, product_found=True, stock=stock)

    def format_lookup_response(self, raw_ref: str) -> str:
        result = self.lookup(raw_ref)
        if result.status == "missing_product":
            return "Référence introuvable."
        if result.status == "missing_stock":
            return f"Référence : {result.ref}\nRéférence trouvée, stock entrepôt non renseigné."
        assert result.stock is not None
        stock = result.stock
        packages = f"{stock.total_packages} paquets" if stock.total_packages is not None else "paquets inconnus"
        lines = [
            f"Référence : {stock.ref}",
            f"Stock entrepôt : {stock.display_text}",
            f"Total : {stock.total_pieces} pièces / {packages}",
            f"Dernière synchro : {self._format_datetime(stock.last_synced_at)}",
        ]
        if stock.notes:
            lines.append(f"Notes : {stock.notes}")
        return "\n".join(lines)

    def _format_datetime(self, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(self.timezone).strftime("%d/%m/%Y %H:%M")

