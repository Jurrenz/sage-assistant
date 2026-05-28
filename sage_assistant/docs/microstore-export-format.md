# Exports Microstore

Le MVP accepte des fichiers `.xlsx` avec une ligne d'en-tetes.

## Export produits

Colonnes reconnues automatiquement :

- reference : `reference`, `ref`, `sku`, `code`, `code article`, `article`
- type : `type`, `categorie`, `famille`, `type article`
- nom : `nom`, `nom produit`, `description`, `designation`
- prix : `prix`, `prix ht`, `pu ht`, `p.u. ht`, `prix unitaire`
- colisage : `colisage`, `pcs/ctn`, `pieces par paquet`, `pack`

La reference est obligatoire.

## Export commande

Colonnes reconnues automatiquement :

- reference
- quantite
- prix optionnel

Si le prix est present dans la commande, il est utilise par defaut pour la ligne Sage.
