# Sage Assistant

Assistant Windows compagnon de Sage 50 pour grossiste textile.

Le MVP est une application desktop Python/PySide6 qui :

- importe un export produits Microstore Excel ;
- garde un cache SQLite local pour resoudre les references commandes ;
- maintient les correspondances type Microstore -> code article Sage/libelle Sage ;
- affiche les commandes Microstore deposees dans un dossier ;
- synchronise les commandes eFashion/PFS par API apres connexion dans l'app ;
- injecte la commande selectionnee dans une facture Sage deja ouverte via AutoHotkey v2.

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

1. Dans `Reglages`, configurer le dossier BDD Microstore et mettre a jour la BDD produits.
2. Ouvrir `Mappings Sage` pour completer les correspondances type Microstore -> code Sage/libelle.
3. Configurer le dossier ou Microstore depose automatiquement les commandes.
4. Ouvrir Sage sur une facture brouillon deja creee.
5. Dans `Commandes`, selectionner une commande.
6. Double-cliquer si besoin pour verifier ou supprimer une ligne.
7. Cliquer `Injecter dans Sage`.
8. Confirmer les controles AHK, puis verifier visuellement les lignes dans Sage.

## Portails eFashion / PFS

Dans `Reglages > Portails`, saisir l'email et le mot de passe du portail, puis cliquer `Connexion eFashion` et/ou `Connexion PFS`.
Le mot de passe n'est pas sauvegarde dans les reglages. Seul l'email peut etre conserve pour eviter de le retaper.

Apres connexion, cliquer `Synchroniser commandes`. Les commandes recuperees par API apparaissent dans la page `Commandes` avec les commandes Microstore et utilisent le meme flux de detail, validation et injection Sage.

## Important

Sage reste le moteur comptable. L'assistant ne cree pas de facture, ne valide pas la facture et ne transmet rien en comptabilite.
