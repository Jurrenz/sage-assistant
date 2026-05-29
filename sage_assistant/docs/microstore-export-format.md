# Exports Microstore

Le MVP accepte des fichiers `.xlsx`, `.xlsm` et `.xls` avec une ligne d'en-tetes.

## Export produits

Colonnes reconnues automatiquement :

- reference : `reference`, `ref`, `sku`, `code`, `code article`, `article`
- type : `type`, `categorie`, `famille`, `type article`
- nom : `nom`, `nom produit`, `description`, `designation`
- prix : `prix`, `prix ht`, `pu ht`, `p.u. ht`, `prix unitaire`
- colisage : `colisage`, `pcs/ctn`, `pieces par paquet`, `pack`

La reference est obligatoire.

## Export commande

Colonnes reconnues automatiquement pour les exports type tableau :

- reference
- quantite
- prix optionnel

Si le prix est present dans la commande, il est utilise par defaut pour la ligne Sage.

## Commande Microstore `.xls` acceptee

Le fichier `.xls` genere quand une commande est acceptee est pris en charge avec ces colonnes :

- `product_reference`
- `quantity`
- `unit`
- `Unit price`

Regle appliquee :

- `quantity` = nombre de paquets
- `unit` = colisage
- quantite Sage = `quantity * unit`
- `Unit price` = P.U. HT retenu
- `Total` est ignore, car il peut valoir `0.0` dans les fichiers reels.

## Dossier commandes automatique

Sur le mini PC Windows, renseigner dans l'onglet `Imports` le dossier ou Microstore depose les commandes acceptees.

L'assistant :

- scanne les fichiers `.xls`, `.xlsx`, `.xlsm` ;
- ignore les fichiers temporaires Excel commencant par `~$` ;
- garde uniquement les fichiers dont le nom est un numero de commande, par exemple `1001627.xls` ;
- lit les infos client disponibles dans le fichier : numero, client, ville, telephone, email, TVA, transport ;
- calcule les totaux operationnels : lignes, paquets, pieces, montant ;
- affiche les commandes dans un tableau trie par date de modification ;
- permet d'importer une commande selectionnee sans ouvrir le selecteur de fichier.

L'import reste volontairement declenche par l'utilisateur pour eviter d'ajouter une mauvaise commande a une facture Sage ouverte.

## Dossier BDD Microstore Google Drive

Pointer `Dossier BDD Microstore` vers le dossier actif :

```text
SZFashion/MS_IMPORT
```

Structure attendue :

```text
MS_IMPORT/
  2026-05-04/
    Modele d'article-....xlsx
  2026-05-29/
    Modele d'article-....xlsx
```

L'assistant :

- scanne recursivement les sous-dossiers dates ;
- cherche les fichiers `.xlsx` ou `.xlsm` dont le nom contient `Modele` et `article` ;
- ignore les fichiers temporaires et `MS_IMPORT_DISABLED` ;
- affiche le dernier export detecte avec date et nombre de references ;
- importe la BDD uniquement quand l'utilisateur clique `Mettre a jour BDD`.
