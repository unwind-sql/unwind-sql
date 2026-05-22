"""Source loader shared by the Python `raw_*` models.

Reads the bundled Parquet fixtures with `pyarrow` — zero-copy registration in
DuckDB when the Python model returns the resulting Arrow table.

For a real project this is where you'd swap in your own ingestion (Postgres
via psycopg, Oracle via oracledb, an S3 scan with `read_parquet`, …) — Unwind
deliberately ships zero DB-third-party dependencies so you stay in control.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_data(name: str) -> Any:
    """Read the bundled parquet fixture for `name` as a `pyarrow.Table`."""
    return pq.read_table(str(_DATA_DIR / f"{name}.parquet"))
