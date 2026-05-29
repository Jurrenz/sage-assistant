from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from pathlib import Path

from .models import InvoiceLine, utc_now_iso
from .settings import (
    AppSettings,
    REAL_SAGE_ONE_LINE_MODE,
    default_ahk_diagnostics_path,
    default_ahk_log_path,
    default_queue_path,
    project_root,
)


def write_injection_queue(
    lines: list[InvoiceLine],
    settings: AppSettings,
    path: Path | None = None,
    line_limit: int | None = None,
) -> Path:
    selected_lines = lines[:line_limit] if line_limit and line_limit > 0 else lines
    if settings.sage_profile.injection_mode == REAL_SAGE_ONE_LINE_MODE:
        if len(selected_lines) != 1:
            raise ValueError("Le mode Sage reel impose exactement 1 ligne a injecter.")
    blocked = [line for line in selected_lines if line.validation_status != "ok"]
    if blocked:
        refs = ", ".join(line.ref for line in blocked[:5])
        raise ValueError(f"Impossible d'injecter: lignes bloquees ({refs})")
    queue_path = path or default_queue_path()
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    if not settings.sage_profile.log_path:
        settings.sage_profile.log_path = str(default_ahk_log_path())
    if not settings.sage_profile.diagnostics_path:
        settings.sage_profile.diagnostics_path = str(default_ahk_diagnostics_path())
    payload = {
        "created_at": utc_now_iso(),
        "profile": asdict(settings.sage_profile),
        "source_line_count": len(lines),
        "line_limit": line_limit or 0,
        "lines": [line.as_injection_dict() for line in selected_lines],
    }
    queue_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return queue_path


def launch_autohotkey(settings: AppSettings, queue_path: Path) -> subprocess.Popen[str]:
    script = project_root() / "automation" / "sage_injector.ahk"
    return subprocess.Popen(
        [settings.autohotkey_path, str(script), str(queue_path)],
        text=True,
    )


def launch_ahk_tool(settings: AppSettings, script_name: str, *args: str) -> subprocess.Popen[str]:
    script = project_root() / "automation" / script_name
    return subprocess.Popen(
        [settings.autohotkey_path, str(script), str(project_root() / "data" / "settings.json"), *args],
        text=True,
    )
