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
