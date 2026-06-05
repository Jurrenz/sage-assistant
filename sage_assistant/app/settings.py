from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


APP_NAME = "Sage Assistant"
REAL_SAGE_ONE_LINE_MODE = "real_sage_one_line"
REAL_SAGE_INJECTION_LABEL = "Injection Sage réelle"
LEGACY_INJECTION_MODES = {"keyboard_only", "calibrated_clicks", "control_based", REAL_SAGE_ONE_LINE_MODE}
SAGE_50_WINDOW_TITLE = "Sage 50 : S.Z FASHION"


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


def default_ahk_diagnostics_path() -> Path:
    return data_dir() / "sage_window_diagnostics.txt"


def default_settings_path() -> Path:
    return data_dir() / "settings.json"


@dataclass
class SageProfile:
    injection_mode: str = REAL_SAGE_ONE_LINE_MODE
    window_title_contains: str = SAGE_50_WINDOW_TITLE
    start_position: str = "article_code"
    delay_ms: int = 80
    after_article_tabs: int = 1
    after_description_tabs: int = 1
    after_quantity_tabs: int = 1
    validate_key: str = "Enter"
    new_line_x: int = 0
    new_line_y: int = 0
    article_cell_x: int = 0
    article_cell_y: int = 0
    line_start_x: int = 0
    line_start_y: int = 0
    focus_guard: bool = True
    step_mode: bool = False
    log_path: str = ""
    diagnostics_path: str = ""


@dataclass
class AppSettings:
    always_on_top: bool = False
    auto_close_with_sage: bool = True
    autohotkey_path: str = "AutoHotkey64.exe"
    sage_executable_path: str = ""
    product_folder_path: str = ""
    last_product_file_path: str = ""
    order_folder_path: str = ""
    injection_line_limit: int = 0
    sage_profile: SageProfile = field(default_factory=SageProfile)


def load_settings(path: Path | None = None) -> AppSettings:
    settings_path = path or default_settings_path()
    if not settings_path.exists():
        settings = AppSettings()
        settings.sage_profile.log_path = str(default_ahk_log_path())
        settings.sage_profile.diagnostics_path = str(default_ahk_diagnostics_path())
        normalize_sage_profile(settings.sage_profile)
        return settings
    raw = json.loads(settings_path.read_text(encoding="utf-8"))
    profile = SageProfile(**raw.get("sage_profile", {}))
    if not profile.log_path:
        profile.log_path = str(default_ahk_log_path())
    if not profile.diagnostics_path:
        profile.diagnostics_path = str(default_ahk_diagnostics_path())
    normalize_sage_profile(profile)
    raw["sage_profile"] = profile
    raw["injection_line_limit"] = 0
    return AppSettings(**raw)


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    normalize_sage_profile(settings.sage_profile)
    settings.injection_line_limit = 0
    settings_path = path or default_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")


def is_windows() -> bool:
    return sys.platform.startswith("win")


def normalize_sage_profile(profile: SageProfile) -> None:
    legacy_mode = profile.injection_mode in LEGACY_INJECTION_MODES
    if legacy_mode:
        profile.injection_mode = REAL_SAGE_ONE_LINE_MODE
    if not profile.window_title_contains or (legacy_mode and profile.window_title_contains == "Sage"):
        profile.window_title_contains = SAGE_50_WINDOW_TITLE
    profile.step_mode = False
