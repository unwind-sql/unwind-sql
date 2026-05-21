"""Génère les 5 tables sources au format Parquet dans example/data/."""

import argparse
import pathlib
import random

import pyarrow as pa
import pyarrow.parquet as pq

DATA_DIR = pathlib.Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

WAREHOUSES = ["WH-PARIS-SUD", "WH-LYON", "WH-MARSEILLE"]
PRODUCTS = ["PROD-A", "PROD-B", "PROD-C", "PROD-D"]
CARRIERS = ["CARRIER-1", "CARRIER-2", "CARRIER-3"]


def generate(n: int = 100_000, seed: int = 42) -> None:
    rng = random.Random(seed)

    # ── raw_orders ───────────────────────────────────────────────────────
    order_ids = [f"ORD-{i:07d}" for i in range(1, n + 1)]
    warehouse_ids = [rng.choice(WAREHOUSES) for _ in range(n)]
    product_ids = [rng.choice(PRODUCTS) for _ in range(n)]
    gross_sales = [round(rng.uniform(50.0, 1000.0), 2) for _ in range(n)]
    qtys = [rng.randint(1, 10) for _ in range(n)]

    # Valeur aberrante : qty = 0 sur une commande
    outlier_idx = rng.randint(0, n - 1)
    qtys[outlier_idx] = 0

    raw_orders = pa.table(
        {
            "order_id": order_ids,
            "warehouse_id": warehouse_ids,
            "product_id": product_ids,
            "gross_sales": gross_sales,
            "qty": qtys,
        }
    )

    # ── raw_shipments (1 expédition par commande) ────────────────────────
    carrier_ids = [rng.choice(CARRIERS) for _ in range(n)]
    weights = [round(rng.uniform(0.3, 8.0), 1) for _ in range(n)]
    distances = [round(rng.uniform(30.0, 600.0), 1) for _ in range(n)]

    # Valeur aberrante : poids de 1500 kg au lieu de ~1.5
    outlier_ship_idx = rng.randint(0, n - 1)
    weights[outlier_ship_idx] = 1500.0

    raw_shipments = pa.table(
        {
            "order_id": order_ids,
            "carrier_id": carrier_ids,
            "weight_kg": weights,
            "distance_km": distances,
        }
    )

    # ── ref_carrier_rates (table de référence, taille fixe) ──────────────
    ref_carrier_rates = pa.table(
        {
            "carrier_id": CARRIERS,
            "cost_per_kg": [0.50, 0.45, 0.60],
            "cost_per_km": [0.12, 0.10, 0.15],
            "fuel_surcharge_pct": [0.05, 0.03, 0.08],
        }
    )

    # ── ref_local_taxes (table de référence, taille fixe) ────────────────
    ref_local_taxes = pa.table(
        {
            "warehouse_id": WAREHOUSES,
            "tax_pct": [0.20, 0.18, 0.15],
            "fixed_handling_fee": [2.50, 1.80, 3.00],
        }
    )

    # ── raw_refunds (~15 % des commandes) ────────────────────────────────
    refund_count = max(1, int(n * 0.15))
    refund_order_ids = rng.sample(order_ids, refund_count)
    refund_amounts = [round(rng.uniform(5.0, 200.0), 2) for _ in range(refund_count)]

    raw_refunds = pa.table(
        {
            "order_id": refund_order_ids,
            "refund_amount": refund_amounts,
        }
    )

    # ── Écriture Parquet ─────────────────────────────────────────────────
    tables = {
        "raw_orders": raw_orders,
        "raw_shipments": raw_shipments,
        "ref_carrier_rates": ref_carrier_rates,
        "ref_local_taxes": ref_local_taxes,
        "raw_refunds": raw_refunds,
    }

    for name, table in tables.items():
        path = DATA_DIR / f"{name}.parquet"
        pq.write_table(table, path)  # type: ignore
        print(f"✓ {path.relative_to(DATA_DIR.parent)}  ({table.num_rows} lignes)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Génère les tables sources Parquet.")
    parser.add_argument(
        "-n",
        "--num-orders",
        type=int,
        default=100_000,
        help="Nombre de commandes à générer (défaut : 100 000)",
    )
    args = parser.parse_args()
    generate(n=args.num_orders)
