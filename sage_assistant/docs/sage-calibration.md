# Calibration Sage

Objectif : verifier la sequence clavier exacte avant d'utiliser l'injection sur une vraie facture.

## Preparation

1. Ouvrir Sage.
2. Choisir une fiche client de test.
3. Creer une facture de test.
4. Placer le curseur au debut d'une ligne article libre.
5. Preparer 2 ou 3 lignes dans l'assistant.

## Sequence MVP par defaut

Pour chaque ligne, AutoHotkey envoie :

1. code article Sage ;
2. `Tab` vers description ;
3. description complete ;
4. `Tab` vers quantite ;
5. quantite en pieces ;
6. `Tab` vers P.U. HT ;
7. prix unitaire HT ;
8. `Enter`.

Les nombres de tabulations et le delai clavier sont configurables dans l'onglet `Reglages`.

## Securite

- Pause/reprise : `Ctrl+Alt+P`
- Stop immediat : `Ctrl+Alt+S`
- Ligne suivante en mode pas-a-pas : `Ctrl+Alt+N`

Toujours tester une nouvelle configuration sur une facture brouillon.

## Premier test conseille

1. Regler `Limite lignes test` a `1`.
2. Garder `Mode pas-a-pas AHK` active.
3. Injecter une facture brouillon.
4. Verifier visuellement article, description/reference, quantite et P.U. HT.
5. Passer a `2` lignes seulement apres validation.
6. Passer a `Toutes` uniquement quand les deux premiers essais sont corrects.
