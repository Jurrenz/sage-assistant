from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


APP_NAME = "Sage Assistant"


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    path = project_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_db_path() -> Path:
    return data_dir() / "app.sqlite"


def default_queue_path() -> Path:
    return data_dir() / "sage_queue.json"


def default_ahk_log_path() -> Path:
    return data_dir() / "sage_injection.log"


def default_settings_path() -> Path:
    return data_dir() / "settings.json"


@dataclass
class SageProfile:
    window_title_contains: str = "Sage"
    start_position: str = "article_code"
    delay_ms: int = 80
    after_article_tabs: int = 1
    after_description_tabs: int = 1
    after_quantity_tabs: int = 1
    validate_key: str = "Enter"
    focus_guard: bool = True
    step_mode: bool = True
    log_path: str = ""


@dataclass
class AppSettings:
    always_on_top: bool = False
    auto_close_with_sage: bool = True
    autohotkey_path: str = "AutoHotkey64.exe"
    sage_executable_path: str = ""
    injection_line_limit: int = 1
    sage_profile: SageProfile = field(default_factory=SageProfile)


def load_settings(path: Path | None = None) -> AppSettings:
    settings_path = path or default_settings_path()
    if not settings_path.exists():
        return AppSettings()
    raw = json.loads(settings_path.read_text(encoding="utf-8"))
    profile = SageProfile(**raw.get("sage_profile", {}))
    if not profile.log_path:
        profile.log_path = str(default_ahk_log_path())
    raw["sage_profile"] = profile
    return AppSettings(**raw)


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    settings_path = path or default_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")


def is_windows() -> bool:
    return sys.platform.startswith("win")
