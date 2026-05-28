# Packaging Windows

Option simple MVP :

```powershell
cd sage_assistant
python -m venv .venv
.venv\Scripts\activate
pip install -e .
launchers\start_sage_assistant.bat
```

Option `.exe` avec PyInstaller :

```powershell
pip install pyinstaller
pyinstaller --name SageAssistant --windowed --add-data "automation;automation" -m app.main
```

Pour le raccourci commun Sage + Assistant, editer `launchers/start_sage_assistant.bat` et decommenter la ligne `start "" "C:\...\Sage50.exe"`.
