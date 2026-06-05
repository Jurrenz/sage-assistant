# Diagnostic Sage

Le mode de calibration par positions souris a ete retire. Le chemin garde est le mode Sage reel :

1. AutoHotkey detecte la fenetre Sage 50.
2. AutoHotkey detecte la facture active `Facture - ...`.
3. AutoHotkey trouve le bouton Win32 `&Ajouter`.
4. AutoHotkey clique uniquement ce bouton.
5. La saisie des lignes se fait ensuite au clavier dans la grille Sage.

## Preparation

1. Ouvrir Sage.
2. Ouvrir une facture brouillon.
3. Selectionner une commande dans Sage Assistant.
4. Cliquer `Injecter dans Sage`.
5. Verifier visuellement les lignes avant toute action Sage.

## Securite

- Pause/reprise : `Ctrl+Alt+P`
- Stop immediat : `Ctrl+Alt+S`
- Captures avant/apres dans `data/captures`
- Log AHK dans `data/sage_injection.log`

## Diagnostic

Le bouton `Diagnostic Sage` reste disponible dans l'application. Il sert uniquement a auditer les fenetres et controles Sage si Sage change de comportement.
