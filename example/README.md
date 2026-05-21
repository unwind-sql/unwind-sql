# Exemple : Rentabilité d'un réseau d'entrepôts e-commerce

Imaginez que vous gérez plusieurs entrepôts qui expédient des commandes à travers la France. Pour chaque commande, vous voulez savoir combien elle vous rapporte _réellement_ une fois tous les coûts déduits : le transport (qui dépend du poids du colis et de la distance), la surcharge carburant du transporteur, les taxes locales de l'entrepôt d'expédition, et les éventuels remboursements clients. Le résultat final agrège tout cela par entrepôt pour identifier lesquels sont rentables et lesquels ne le sont pas.

Ce pipeline SQL modélise exactement ce calcul, étape par étape : on part des commandes brutes, on enrichit avec les coûts de transport, on ajoute la fiscalité locale, on soustrait les remboursements, puis on agrège par entrepôt. Chaque étape est une requête SQL lisible et indépendante — c'est ce découpage qu'Unwind sait analyser, optimiser et investiguer.

---

Le cas d'usage parfait pour cela est le calcul de la Marge Nette par Commande (Landed Cost & Net Margin) dans le e-commerce ou la logistique.

C'est un classique qui génère souvent des "double scans" dans les pipelines si les étapes ne sont pas fusionnées par un moteur intelligent (comme DuckDB ou le moteur Catalyst de Spark), car on a tendance à empiler les vues pour la lisibilité.

## 1. Les 5 Tables Sources

- `raw_orders` : order_id, warehouse_id, product_id, gross_sales, qty
- `raw_shipments` : order_id, carrier_id, weight_kg, distance_km
- `ref_carrier_rates` : carrier_id, cost_per_kg, cost_per_km, fuel_surcharge_pct
- `ref_local_taxes` : warehouse_id, tax_pct, fixed_handling_fee
- `raw_refunds` : order_id, refund_amount

raw: donnée brute
int: table intermédiaire
fct: table de faits (agrégée)

`raw_orders` est un **modèle Python** ([raw_orders.py](models/raw_orders.py)) qui
délègue à `helpers.load_data()`. Bascule entre Parquet et Oracle (ou tout
SQLAlchemy) avec une variable d'environnement, sans toucher au SQL :

```bash
UNWIND_SOURCE_MODE=parquet    uv run python main.py   # défaut
UNWIND_SOURCE_MODE=oracle     uv run python main.py
UNWIND_SOURCE_MODE=sqlalchemy uv run python main.py
```

Les autres `raw_*` restent en SQL parce qu'ils sont déjà des `SELECT * FROM
read_parquet(...)`. Migration possible un par un.

## 2. La Macro Réutilisable (Utilisée 2 fois)

Nous allons créer une macro générique pour calculer des frais qui comportent à la fois une part variable (pourcentage) et une part fixe.

👉 Voir [macros/apply_fee.sql](macros/apply_fee.sql)

## 3. Le Pipeline en 5 Étapes (Idéal pour l'optimisation)

Voici le pipeline tel qu'il est écrit par le Data Engineer (orienté lisibilité et modularité).

### Étape 1 : Préparation et Jointures (Le "Socle")

**Table : `int_order_base`**
On rassemble toutes les dimensions nécessaires et on filtre les commandes valides.

👉 Voir [int_order_base.sql](int_order_base.sql)

### Étape 2 : Calcul des Coûts de Transport de Base (Candidat Fusion)

**Table : `int_transport_costs`**
On calcule le coût de base, puis on utilise la macro pour la surcharge carburant.

👉 Voir [int_transport_costs.sql](int_transport_costs.sql)

### Étape 3 : Calcul des Taxes Locales (Candidat Fusion)

**Table : `int_tax_costs`**
On ajoute la couche fiscale. On lit depuis la table précédente, mais on reste sur le même grain (order_id).

👉 Voir [int_tax_costs.sql](int_tax_costs.sql)

### Étape 4 : Marge Nette par Commande (Jointure Tardive)

**Table : `int_net_margin_per_order`**
On intègre les remboursements éventuels et on fait l'équation finale.

👉 Voir [int_net_margin_per_order.sql](int_net_margin_per_order.sql)

### Étape 5 : Agrégation (Résultat Final)

**Table : `fct_warehouse_profitability`**

👉 Voir [fct_warehouse_profitability.sql](fct_warehouse_profitability.sql)

## 🌟 Pourquoi ce use case est brillant pour votre framework ?

### 1. Le potentiel de Merge / Query Folding (Éviter les double scans)

Les étapes 2 et 3 lisent séquentiellement la même structure sans faire de `GROUP BY` ni de `JOIN`. Si votre moteur d'investigation ou d'exécution (comme DuckDB) est intelligent, il va détecter qu'il s'agit de simples projections linéaires (`SELECT *`).

Lors de l'exécution, l'Étape 2 et l'Étape 3 seront fusionnées en une seule passe en mémoire sur les résultats de l'Étape 1 :

```sql
-- Ce que le moteur exécutera réellement (Fusion E1 + E2 + E3) :
SELECT
    o.order_id,
    -- ... [colonnes de l'étape 1] ...
    (s.weight_kg * c.cost_per_kg) + (s.distance_km * c.cost_per_km) AS base_shipping_cost,
    ROUND((((s.weight_kg * c.cost_per_kg) + (s.distance_km * c.cost_per_km)) * COALESCE(c.fuel_surcharge_pct, 0)) + 0, 2) AS fuel_surcharge_fee,
    ROUND((o.gross_sales * COALESCE(t.tax_pct, 0)) + COALESCE(t.fixed_handling_fee, 0), 2) AS local_tax_fee
FROM raw_orders o ... [JOINs] ...
```

Votre Agent LLM de lineage devra comprendre ce "folding" : s'il enquête sur `local_tax_fee`, il ne doit pas s'arrêter à `int_transport_costs` en pensant que c'est une table physiqueisée, mais bien redescendre au socle `int_order_base`.

### 2. L'investigation Cell/Value

Si la cellule `total_net_margin` pour l'entrepôt "Paris-Sud" dans `fct_warehouse_profitability` est anormalement basse, l'Agent devra :

- Descendre le `GROUP BY` (Étape 5) pour trouver quelles commandes spécifiques (Row IDs de `int_net_margin_per_order`) tirent la moyenne vers le bas.
- Découvrir si le problème vient de `gross_sales` (erreur de prix de base), d'un pic de `refund_amount` (table `raw_refunds`), ou d'une explosion de `fuel_surcharge_fee`.
- S'il s'agit du `fuel_surcharge_fee`, le LLM devra "déplier" l'Étape 2, comprendre la macro, et potentiellement identifier dans `raw_shipments` une valeur aberrante où un `weight_kg` a été entré à "1500" au lieu de "1.5".
