from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .excel_import import import_products


PRODUCT_FILE_PATTERNS = ("*.xlsx", "*.xlsm")
DISABLED_DIR_NAME = "MS_IMPORT_DISABLED"


@dataclass(frozen=True)
class ProductExportFile:
    path: Path
    modified_at: float
    product_count: int = 0
    warning_count: int = 0
    error: str = ""

    @property
    def modified_at_label(self) -> str:
        return datetime.fromtimestamp(self.modified_at).strftime("%d/%m/%Y %H:%M")

    @property
    def display_name(self) -> str:
        status = f"{self.product_count} refs" if not self.error else f"erreur: {self.error}"
        return f"{self.path.name} - {self.modified_at_label} - {status}"


def list_product_exports(folder: str | Path) -> list[ProductExportFile]:
    return [summarize_product_export(path) for path in _list_product_export_paths(folder)]


def latest_product_export(folder: str | Path) -> ProductExportFile | None:
    paths = _list_product_export_paths(folder)
    return summarize_product_export(paths[0]) if paths else None


def _list_product_export_paths(folder: str | Path) -> list[Path]:
    folder_path = Path(folder).expanduser()
    if not folder_path.exists() or not folder_path.is_dir():
        return []

    files: list[Path] = []
    for pattern in PRODUCT_FILE_PATTERNS:
        for path in folder_path.rglob(pattern):
            if not path.is_file() or path.name.startswith("~$"):
                continue
            if _is_disabled_path(path) or not _looks_like_product_export(path):
                continue
            files.append(path)

    return sorted(files, key=lambda item: (item.stat().st_mtime, item.name), reverse=True)


def summarize_product_export(path: str | Path) -> ProductExportFile:
    file_path = Path(path)
    modified_at = file_path.stat().st_mtime
    try:
        result = import_products(file_path)
        return ProductExportFile(
            path=file_path,
            modified_at=modified_at,
            product_count=len(result.rows),
            warning_count=len(result.warnings),
        )
    except Exception as exc:
        return ProductExportFile(path=file_path, modified_at=modified_at, error=str(exc))


def _looks_like_product_export(path: Path) -> bool:
    normalized = _normalize_filename(path.name)
    return "modele" in normalized and "article" in normalized


def _is_disabled_path(path: Path) -> bool:
    return any(part.upper() == DISABLED_DIR_NAME for part in path.parts)


def _normalize_filename(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).lower()
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.replace("_", " ").replace("-", " ")
