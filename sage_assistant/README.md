# Sage Assistant

Assistant Windows compagnon de Sage 50 pour grossiste textile.

Le MVP est une application desktop Python/PySide6 qui :

- importe un export produits Microstore Excel ;
- garde un cache SQLite local pour recherche/autocomplete ;
- maintient les correspondances type Microstore -> code article Sage/libelle Sage ;
- prépare des lignes de facture manuelles ou depuis un Excel commande ;
- exporte une file JSON injectee dans Sage par AutoHotkey v2.
- importe les commandes Microstore `.xls` generees apres acceptation.

## Demarrage developpement

```powershell
cd sage_assistant
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
sage-assistant
```

Sur macOS/Linux pour tests metier :

```bash
cd sage_assistant
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Usage MVP

1. Importer l'export produits Microstore.
2. Completer les mappings type Microstore -> code Sage/libelle.
3. Ajouter des lignes par recherche reference ou importer une commande Excel.
4. Verifier les lignes.
5. Cliquer `Preparer injection Sage`.
6. Pour un premier test Sage, garder `Limite lignes test = 1` et `Mode pas-a-pas AHK`.
7. Sur Windows, lancer AutoHotkey v2 avec `automation/sage_injector.ahk` ou laisser l'app le lancer si le chemin AHK est configure.

## Important

Sage reste le moteur comptable. L'assistant ne modifie pas Sage directement par API et ne tente pas de reverse-engineer le clipboard Sage.
