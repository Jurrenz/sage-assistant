from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from app.db import Database
from telegram_bot.commands.stock import (
    HELP_TEXT,
    authorize,
    handle_chatid_text,
    handle_ping_text,
    handle_stock_text,
    handle_syncstock_text,
)
from telegram_bot.config import load_bot_settings
from telegram_bot.database.warehouse_repository import WarehouseRepository
from telegram_bot.services.auth_service import AuthService
from telegram_bot.services.stock_import_service import StockImportService
from telegram_bot.services.stock_service import StockService


LOGGER = logging.getLogger(__name__)


async def _reply(update: Update, text: str) -> None:
    if update.effective_message is not None:
        await update.effective_message.reply_text(text)


def _services(context: ContextTypes.DEFAULT_TYPE) -> tuple[AuthService, StockService, StockImportService, WarehouseRepository]:
    return (
        context.application.bot_data["auth_service"],
        context.application.bot_data["stock_service"],
        context.application.bot_data["stock_import_service"],
        context.application.bot_data["warehouse_repository"],
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    auth_service, _, _, _ = _services(context)
    denied = authorize(update.effective_chat.id if update.effective_chat else None, auth_service)
    if denied:
        await _reply(update, denied.text)
        return
    await _reply(update, HELP_TEXT)


async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    auth_service, stock_service, _, _ = _services(context)
    denied = authorize(update.effective_chat.id if update.effective_chat else None, auth_service)
    if denied:
        await _reply(update, denied.text)
        return
    text = update.effective_message.text if update.effective_message else ""
    await _reply(update, handle_stock_text(text or "", stock_service))


async def syncstock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    auth_service, _, import_service, _ = _services(context)
    denied = authorize(update.effective_chat.id if update.effective_chat else None, auth_service)
    if denied:
        await _reply(update, denied.text)
        return
    stock_path = context.application.bot_data["stock_path"]
    await _reply(update, handle_syncstock_text(import_service, stock_path))


async def chatid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    LOGGER.info(
        "Telegram chat id=%s type=%s title=%s user_id=%s",
        chat.id if chat else None,
        chat.type if chat else "",
        chat.title if chat else "",
        user.id if user else None,
    )
    await _reply(
        update,
        handle_chatid_text(
            chat.id if chat else None,
            chat.type if chat else "",
            chat.title if chat and chat.title else "",
            user.id if user else None,
        ),
    )


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    auth_service, _, _, repository = _services(context)
    denied = authorize(update.effective_chat.id if update.effective_chat else None, auth_service)
    if denied:
        await _reply(update, denied.text)
        return
    db = context.application.bot_data["database"]
    stock_path = context.application.bot_data["stock_path"]
    await _reply(update, handle_ping_text(db, repository, str(stock_path)))


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await stock_command(update, context)


async def shutdown(application) -> None:
    db = application.bot_data.get("database")
    if db is not None:
        db.close()


def build_application():
    settings = load_bot_settings()
    if not settings.bot_token:
        raise RuntimeError("Token Telegram manquant dans data/telegram_bot_settings.json ou SAGE_ASSISTANT_TELEGRAM_TOKEN.")

    db = Database()
    repository = WarehouseRepository(db)
    application = ApplicationBuilder().token(settings.bot_token).post_shutdown(shutdown).build()
    application.bot_data["database"] = db
    application.bot_data["warehouse_repository"] = repository
    application.bot_data["auth_service"] = AuthService(settings.allowed_chat_ids)
    application.bot_data["stock_service"] = StockService(repository, settings.timezone)
    application.bot_data["stock_import_service"] = StockImportService(repository)
    application.bot_data["stock_path"] = settings.stock_import_path
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("start", help_command))
    application.add_handler(CommandHandler("chatid", chatid_command))
    application.add_handler(CommandHandler("ping", ping_command))
    application.add_handler(CommandHandler("stock", stock_command))
    application.add_handler(CommandHandler("syncstock", syncstock_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
    return application


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    LOGGER.info("Demarrage du bot Telegram Sage Assistant")
    build_application().run_polling()


if __name__ == "__main__":
    main()
