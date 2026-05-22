"""Shared pytest fixtures."""

from __future__ import annotations

import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest


@pytest.fixture
def example_models_dir(fixture_data_dir: Path) -> Path:
    """Path to the bundled example project (`.sql` + `.py` models, 1 macro).

    Returns a fresh copy of `example/models/` paired with canonical parquet
    sources in a sibling `../data/` directory, so tests that auto-render or
    materialize views (e.g. column lineage) work even when `example/data/`
    is absent (parquets are gitignored in CI).
    """
    return fixture_data_dir


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Build an isolated minimal project on disk and return its root."""
    (tmp_path / "macros").mkdir()
    (tmp_path / "macros" / "fmt.sql").write_text(
        "{% macro plus_one(col) %}({{ col }} + 1){% endmacro %}\n",
        encoding="utf-8",
    )
    (tmp_path / "stg_orders.sql").write_text(
        "SELECT id, {{ plus_one('qty') }} AS qty_plus_one FROM raw_orders;\n",
        encoding="utf-8",
    )
    (tmp_path / "fct_orders.sql").write_text(
        "SELECT * FROM stg_orders WHERE created_at = '{{ d_reporting }}';\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture(scope="session")
def fixture_data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A standalone test project: a copy of `example/models/` paired with a small,
    hand-crafted parquet dataset written under a sibling `../data/` directory.

    Decouples the test suite from `example/data/`, which `generate_data.py` can
    inflate to 100k+ rows. The 11 orders here are sized and shaped so every
    trace/runner/web assertion lands on exact named values:
      - ORD-7892 has gross_sales=500 and an outlier weight_kg=1500
      - WH-PARIS-SUD has 4 orders (250, 120, 175, 500) summing to 1045
      - ORD-1009 has qty=0 → filtered out by int_order_base.
    """
    root = tmp_path_factory.mktemp("unwind_fixture")
    models_dir = root / "models"
    data_dir = root / "data"

    src_models = Path(__file__).resolve().parent.parent / "example" / "models"
    shutil.copytree(src_models, models_dir)
    data_dir.mkdir()
    _write_canonical_parquets(data_dir)
    return models_dir


@pytest.fixture
def example_data_ready(fixture_data_dir: Path) -> Path:
    """Stable alias used across trace/runner/web/investigator tests."""
    return fixture_data_dir


def _write_canonical_parquets(data_dir: Path) -> None:
    raw_orders = pa.table(
        {
            "order_id": [
                "ORD-1001", "ORD-1002", "ORD-1003", "ORD-1004", "ORD-1005",
                "ORD-1006", "ORD-1007", "ORD-1008", "ORD-1009", "ORD-1010",
                "ORD-7892",
            ],
            "warehouse_id": [
                "WH-PARIS-SUD", "WH-LYON", "WH-PARIS-SUD", "WH-MARSEILLE", "WH-PARIS-SUD",
                "WH-LYON", "WH-MARSEILLE", "WH-LYON", "WH-MARSEILLE", "WH-LYON",
                "WH-PARIS-SUD",
            ],
            "product_id": [
                "PROD-A", "PROD-B", "PROD-A", "PROD-C", "PROD-B",
                "PROD-D", "PROD-A", "PROD-C", "PROD-D", "PROD-A",
                "PROD-B",
            ],
            "gross_sales": [
                250.0, 500.0, 120.0, 800.0, 175.0,
                95.0, 410.0, 65.0, 999.0, 88.0,
                500.0,
            ],
            "qty": [2, 5, 1, 3, 2, 1, 4, 1, 0, 2, 3],
        }
    )  # fmt: skip
    raw_shipments = pa.table(
        {
            "order_id": [
                "ORD-1001", "ORD-1002", "ORD-1003", "ORD-1004", "ORD-1005",
                "ORD-1006", "ORD-1007", "ORD-1008", "ORD-1009", "ORD-1010",
                "ORD-7892",
            ],
            "carrier_id": [
                "CARRIER-1", "CARRIER-2", "CARRIER-1", "CARRIER-3", "CARRIER-2",
                "CARRIER-1", "CARRIER-3", "CARRIER-1", "CARRIER-2", "CARRIER-1",
                "CARRIER-2",
            ],
            "weight_kg": [
                1.5, 4.0, 0.8, 6.0, 2.5,
                1.0, 3.0, 0.5, 2.5, 1.2,
                1500.0,  # ORD-7892: outlier
            ],
            "distance_km": [
                100.0, 250.0, 80.0, 380.0, 200.0,
                90.0, 350.0, 60.0, 220.0, 100.0,
                280.0,
            ],
        }
    )  # fmt: skip
    ref_carrier_rates = pa.table(
        {
            "carrier_id": ["CARRIER-1", "CARRIER-2", "CARRIER-3"],
            "cost_per_kg": [0.50, 0.45, 0.60],
            "cost_per_km": [0.12, 0.10, 0.15],
            "fuel_surcharge_pct": [0.05, 0.03, 0.08],
        }
    )
    ref_local_taxes = pa.table(
        {
            "warehouse_id": ["WH-PARIS-SUD", "WH-LYON", "WH-MARSEILLE"],
            "tax_pct": [0.20, 0.18, 0.15],
            "fixed_handling_fee": [2.50, 1.80, 3.00],
        }
    )
    raw_refunds = pa.table(
        {
            "order_id": ["ORD-1002", "ORD-1004", "ORD-1007"],
            "refund_amount": [50.0, 100.0, 25.0],
        }
    )

    tables = {
        "raw_orders": raw_orders,
        "raw_shipments": raw_shipments,
        "ref_carrier_rates": ref_carrier_rates,
        "ref_local_taxes": ref_local_taxes,
        "raw_refunds": raw_refunds,
    }
    for name, table in tables.items():
        pq.write_table(table, data_dir / f"{name}.parquet")
