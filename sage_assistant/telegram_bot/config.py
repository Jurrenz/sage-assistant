from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from app.settings import data_dir, project_root


def default_stock_path() -> Path:
    return project_root().parent / "stock.xlsx"


def default_bot_settings_path() -> Path:
    return data_dir() / "telegram_bot_settings.json"


def default_bot_settings_payload() -> dict[str, object]:
    return {
        "bot_token": "",
        "allowed_chat_ids": [],
        "stock_import_path": str(default_stock_path()),
        "timezone": "Europe/Paris",
    }


@dataclass(frozen=True)
class TelegramBotSettings:
    bot_token: str = ""
    allowed_chat_ids: set[int] = field(default_factory=set)
    stock_import_path: Path = field(default_factory=default_stock_path)
    timezone: str = "Europe/Paris"


def _parse_chat_ids(value: object) -> set[int]:
    if value in (None, ""):
        return set()
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace(";", ",").split(",")]
    elif isinstance(value, list):
        parts = [str(part).strip() for part in value]
    else:
        parts = [str(value).strip()]
    ids: set[int] = set()
    for part in parts:
        if part:
            ids.add(int(part))
    return ids


def load_bot_settings(path: Path | None = None) -> TelegramBotSettings:
    settings_path = path or default_bot_settings_path()
    raw: dict[str, object] = {}
    if settings_path.exists():
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
    else:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        raw = default_bot_settings_payload()
        settings_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")

    token = str(os.environ.get("SAGE_ASSISTANT_TELEGRAM_TOKEN") or raw.get("bot_token") or "")
    allowed = os.environ.get("SAGE_ASSISTANT_TELEGRAM_CHAT_IDS") or raw.get("allowed_chat_ids")
    stock_path = os.environ.get("SAGE_ASSISTANT_STOCK_XLSX") or raw.get("stock_import_path") or ""
    timezone = str(os.environ.get("SAGE_ASSISTANT_TELEGRAM_TIMEZONE") or raw.get("timezone") or "Europe/Paris")

    return TelegramBotSettings(
        bot_token=token.strip(),
        allowed_chat_ids=_parse_chat_ids(allowed),
        stock_import_path=Path(str(stock_path)).expanduser() if stock_path else default_stock_path(),
        timezone=timezone,
    )
