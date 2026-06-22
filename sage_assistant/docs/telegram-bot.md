# Bot Telegram Stock Entrepot

Le bot Telegram est un module du projet Sage Assistant. La table SQLite `warehouse_stock` est la source de verite V1. Le fichier `stock.xlsx` sert seulement a pre-remplir manuellement la base avec `/syncstock`.

## Installation Windows vierge

```powershell
cd sage_assistant
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```

Au premier lancement, le bot cree automatiquement `data\telegram_bot_settings.json` si le fichier n'existe pas.

Remplir ensuite ce fichier local :

```json
{
  "bot_token": "TOKEN_BOTFATHER",
  "allowed_chat_ids": [],
  "stock_import_path": "C:\\stock.xlsx",
  "timezone": "Europe/Paris"
}
```

`data\telegram_bot_settings.json` est ignore par git. Ne pas mettre le token dans le code.

## Lancement

```powershell
launchers\start_telegram_bot.bat
```

Le log est ecrit dans `data\telegram_bot.log`.

## Premier test

Dans Telegram, envoyer au bot :

```text
/chatid
/ping
/help
```

Si `allowed_chat_ids` est vide, le bot accepte les chats qui lui parlent. Si une liste est configuree, seuls ces chats sont autorises.

## Stock

Consulter une reference :

```text
/stock CM217-1
CM217-1
```

Pre-remplir manuellement depuis Excel si besoin :

```text
/syncstock
```

Il n'y a pas de synchronisation automatique Excel en V1, pour eviter d'ecraser la table SQLite.
