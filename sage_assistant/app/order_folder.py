from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ORDER_FILE_PATTERNS = ("*.xls", "*.xlsx", "*.xlsm")


@dataclass(frozen=True)
class OrderFile:
    path: Path
    modified_at: float

    @property
    def display_name(self) -> str:
        return f"{self.path.name}"


def list_order_files(folder: str | Path) -> list[OrderFile]:
    folder_path = Path(folder).expanduser()
    if not folder_path.exists() or not folder_path.is_dir():
        return []

    files: list[OrderFile] = []
    for pattern in ORDER_FILE_PATTERNS:
        for path in folder_path.glob(pattern):
            if not path.is_file() or path.name.startswith("~$"):
                continue
            if not path.stem.isdigit():
                continue
            files.append(OrderFile(path=path, modified_at=path.stat().st_mtime))

    return sorted(files, key=lambda item: (item.modified_at, item.path.name), reverse=True)


def latest_order_file(folder: str | Path) -> Path | None:
    files = list_order_files(folder)
    return files[0].path if files else None
