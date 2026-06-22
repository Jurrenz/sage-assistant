@echo off
setlocal
cd /d "%~dp0\.."

if not exist "data" mkdir "data"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m telegram_bot.bot >> "data\telegram_bot.log" 2>&1
) else (
  python -m telegram_bot.bot >> "data\telegram_bot.log" 2>&1
)
