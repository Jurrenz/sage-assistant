from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from pathlib import Path

from .models import InvoiceLine, utc_now_iso
from .settings import AppSettings, default_queue_path, project_root


def write_injection_queue(
    lines: list[InvoiceLine],
    settings: AppSettings,
    path: Path | None = None,
) -> Path:
    blocked = [line for line in lines if line.validation_status != "ok"]
    if blocked:
        refs = ", ".join(line.ref for line in blocked[:5])
        raise ValueError(f"Impossible d'injecter: lignes bloquees ({refs})")
    queue_path = path or default_queue_path()
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": utc_now_iso(),
        "profile": asdict(settings.sage_profile),
        "lines": [line.as_injection_dict() for line in lines],
    }
    queue_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return queue_path


def launch_autohotkey(settings: AppSettings, queue_path: Path) -> subprocess.Popen[str]:
    script = project_root() / "automation" / "sage_injector.ahk"
    return subprocess.Popen(
        [settings.autohotkey_path, str(script), str(queue_path)],
        text=True,
    )
