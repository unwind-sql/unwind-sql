"""Source loader shared by the Python `raw_*` models.

Switches between Parquet files on disk and an in-memory pull from Oracle (or
any SQLAlchemy-compatible database) based on the `UNWIND_SOURCE_MODE`
environment variable. The downstream SQL models don't know — they just see
tables named `raw_orders`, `raw_refunds`, etc.

Usage:
    UNWIND_SOURCE_MODE=parquet    uv run python main.py   # default
    UNWIND_SOURCE_MODE=oracle     uv run python main.py
    UNWIND_SOURCE_MODE=sqlalchemy uv run python main.py
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from unwind.connectors import oracle, parquet, sqlalchemy

# Resolve `example/data/` no matter where main.py is invoked from.
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_data(name: str) -> Any:
    """Return the source relation for `name` based on `UNWIND_SOURCE_MODE`."""
    mode = os.environ.get("UNWIND_SOURCE_MODE", "parquet")

    if mode == "parquet":
        return parquet(_DATA_DIR / f"{name}.parquet")

    if mode == "oracle":
        return oracle(
            query=f"SELECT * FROM analytics.{name.upper()}",
            dsn=os.environ["ORACLE_DSN"],
            user=os.environ.get("ORACLE_USER"),
            password=os.environ.get("ORACLE_PASSWORD"),
        )

    if mode == "sqlalchemy":
        return sqlalchemy(
            os.environ["DB_URL"],
            f"SELECT * FROM analytics.{name}",
        )

    raise ValueError(
        f"unknown UNWIND_SOURCE_MODE={mode!r}; "
        "expected 'parquet', 'oracle', or 'sqlalchemy'"
    )
