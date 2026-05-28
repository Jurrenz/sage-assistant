@echo off
setlocal
cd /d "%~dp0\.."

REM Optional: configure Sage path in data\settings.json or paste it below.
REM start "" "C:\Program Files\Sage\Sage 50\Sage50.exe"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m app.main
) else (
  python -m app.main
)
