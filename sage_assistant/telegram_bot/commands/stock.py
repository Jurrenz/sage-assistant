from __future__ import annotations

from dataclasses import dataclass

from telegram_bot.services.auth_service import AuthService
from app.db import Database
from telegram_bot.database.warehouse_repository import WarehouseRepository
from telegram_bot.services.stock_import_service import StockImportService
from telegram_bot.services.stock_service import StockService


HELP_TEXT = "\n".join(
    [
        "Commandes disponibles :",
        "/stock REF - consulter le stock entrepôt",
        "/syncstock - préremplir depuis stock.xlsx manuellement",
        "/chatid - afficher l'identifiant du chat Telegram",
        "/ping - vérifier bot, SQLite et dernière synchro stock",
        "/help - afficher l'aide",
        "",
        "Source de vérité V1 : table SQLite warehouse_stock.",
    ]
)


@dataclass(frozen=True)
class CommandResponse:
    text: str
    allowed: bool = True


def extract_reference(text: str) -> str:
    parts = text.strip().split()
    if not parts:
        return ""
    if parts[0].startswith("/"):
        return parts[1] if len(parts) > 1 else ""
    return parts[0]


def handle_stock_text(text: str, stock_service: StockService) -> str:
    ref = extract_reference(text)
    if not ref:
        return "Indique une référence, par exemple /stock CM217-1."
    return stock_service.format_lookup_response(ref)


def handle_syncstock_text(import_service: StockImportService, stock_path) -> str:
    summary = import_service.sync_from_xlsx(stock_path)
    message = f"Préremplissage Excel terminé : {summary.imported} références importées, {summary.ignored} ignorées."
    if summary.ignored_refs:
        preview = ", ".join(summary.ignored_refs[:10])
        suffix = "..." if len(summary.ignored_refs) > 10 else ""
        message += f"\nRéférences ignorées : {preview}{suffix}"
    return message


def handle_chatid_text(chat_id: int | None, chat_type: str, chat_title: str, user_id: int | None) -> str:
    lines = [
        f"Chat ID : {chat_id if chat_id is not None else 'inconnu'}",
        f"Type : {chat_type or 'inconnu'}",
    ]
    if chat_title:
        lines.append(f"Titre : {chat_title}")
    if user_id is not None:
        lines.append(f"User ID : {user_id}")
    return "\n".join(lines)


def handle_ping_text(db: Database, repository: WarehouseRepository, last_sync_path: str | None = None) -> str:
    db.conn.execute("SELECT 1").fetchone()
    latest = repository.latest_stock_sync() or "aucune"
    lines = [
        "Bot en ligne",
        "Base SQLite OK",
        f"Produits : {db.count_products()}",
        f"Stocks entrepôt : {repository.count_stock_rows()}",
        f"Dernière synchro : {latest}",
    ]
    if last_sync_path:
        lines.append(f"Import Excel configuré : {last_sync_path}")
    return "\n".join(lines)


def authorize(chat_id: int | None, auth_service: AuthService) -> CommandResponse | None:
    if auth_service.is_allowed(chat_id):
        return None
    return CommandResponse("Accès refusé.", allowed=False)
