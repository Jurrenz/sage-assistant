from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path

from .models import InvoiceLine, utc_now_iso
from .settings import (
    AppSettings,
    REAL_SAGE_ONE_LINE_MODE,
    default_ahk_diagnostics_path,
    default_ahk_log_path,
    default_queue_path,
    normalize_sage_profile,
    project_root,
)


def write_injection_queue(
    lines: list[InvoiceLine],
    settings: AppSettings,
    path: Path | None = None,
    line_limit: int | None = None,
) -> Path:
    normalize_sage_profile(settings.sage_profile)
    line_limit = 0
    selected_lines = lines
    blocked = [line for line in selected_lines if line.validation_status != "ok"]
    if blocked:
        refs = ", ".join(line.ref for line in blocked[:5])
        raise ValueError(f"Impossible d'injecter: lignes bloquees ({refs})")
    queue_path = path or Path(tempfile.gettempdir()) / f"sage_assistant_queue_{utc_now_iso().replace(':', '').replace('-', '')}.json"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    if not settings.sage_profile.log_path:
        settings.sage_profile.log_path = str(default_ahk_log_path())
    if not settings.sage_profile.diagnostics_path:
        settings.sage_profile.diagnostics_path = str(default_ahk_diagnostics_path())
    settings.sage_profile.stable_pause_ms = settings.sage_profile.delay_ms
    control_path = queue_path.with_suffix(".control")
    control_path.write_text("", encoding="utf-8")
    payload = {
        "created_at": utc_now_iso(),
        "control_path": str(control_path),
        "profile": asdict(settings.sage_profile),
        "source_line_count": len(lines),
        "line_limit": 0,
        "lines": [line.as_injection_dict() for line in selected_lines],
    }
    queue_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return queue_path


def _resolve_autohotkey(settings: AppSettings) -> Path | str:
    configured = settings.autohotkey_path.strip() or "AutoHotkey64.exe"
    configured_path = Path(configured)
    candidates: list[Path | str] = [configured_path if configured_path.is_absolute() else configured]
    if not configured_path.is_absolute():
        candidates.extend(
            [
                Path("C:/Program Files/AutoHotkey/v2/AutoHotkey64.exe"),
                Path("C:/Program Files/AutoHotkey/AutoHotkey64.exe"),
                Path("C:/Program Files/AutoHotkey/AutoHotkey.exe"),
                Path("C:/Program Files (x86)/AutoHotkey/AutoHotkey.exe"),
            ]
        )
    for candidate in candidates:
        if isinstance(candidate, Path):
            if candidate.exists():
                return candidate
        else:
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
    searched = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(
        "AutoHotkey introuvable.\n"
        f"Valeur configuree: {configured}\n"
        f"Chemins testes: {searched}\n"
        "Installe AutoHotkey v2 ou renseigne le chemin complet de AutoHotkey64.exe dans Reglages > Sage."
    )


def launch_autohotkey(settings: AppSettings, queue_path: Path) -> subprocess.Popen[str]:
    script = project_root() / "automation" / "sage_injector.ahk"
    if not script.exists():
        raise FileNotFoundError(f"Script AHK introuvable: {script}")
    autohotkey = _resolve_autohotkey(settings)
    return subprocess.Popen(
        [str(autohotkey), str(script), str(queue_path)],
        text=True,
    )


def launch_ahk_tool(settings: AppSettings, script_name: str, *args: str) -> subprocess.Popen[str]:
    script = project_root() / "automation" / script_name
    if not script.exists():
        raise FileNotFoundError(f"Script AHK introuvable: {script}")
    autohotkey = _resolve_autohotkey(settings)
    return subprocess.Popen(
        [str(autohotkey), str(script), str(project_root() / "data" / "settings.json"), *args],
        text=True,
    )
