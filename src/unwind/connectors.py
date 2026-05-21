"""Small helpers used by Python models to fetch relations from common sources.

Each helper returns an object that DuckDB can `register()` directly — typically
a `pyarrow.Table` — so a Python model can do:

    from unwind.connectors import parquet, oracle, sqlalchemy

    def model(context):
        return parquet("data/raw_orders.parquet")
        # or:
        # return oracle(dsn="...", query="SELECT * FROM orders")
        # or:
        # return sqlalchemy("oracle+oracledb://...", "SELECT * FROM orders")

Heavy dependencies (`oracledb`, `sqlalchemy`, `pyarrow`) are imported lazily so
this module stays cheap to import.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def parquet(path: str | Path) -> Any:
    """Read a Parquet file and return a `pyarrow.Table`.

    Args:
        path: Filesystem path (or URI supported by pyarrow) of the parquet
            file. A directory containing partitioned parquet files also works.
    """
    import pyarrow.parquet as pq  # noqa: PLC0415

    return pq.read_table(str(path))


def oracle(
    *,
    query: str,
    dsn: str,
    user: str | None = None,
    password: str | None = None,
    config_dir: str | None = None,
) -> Any:
    """Run `query` against an Oracle database and return a `pyarrow.Table`.

    Uses `python-oracledb`. If the installed version exposes Arrow-native
    fetching (`fetch_df_all`, available since 2.x), it's used for a fast
    columnar transfer; otherwise the function falls back to a row-by-row
    fetch converted into Arrow at the end.

    Args:
        query: The SELECT statement to execute. Use bind parameters in the SQL
            (`:name` syntax) and pass them via your own wrapper — keeping the
            helper signature minimal.
        dsn: Oracle DSN (e.g. `host:port/service_name` or a TNS entry name).
        user, password: Credentials. If both are `None`, external auth is
            attempted (wallet, OS auth, etc.).
        config_dir: Optional path to a TNS_ADMIN-style config directory.
    """
    try:
        oracledb = importlib.import_module("oracledb")
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "unwind.connectors.oracle requires the `oracledb` package; install with "
            "`uv pip install oracledb` (or `pip install oracledb`)"
        ) from exc

    connect_kwargs: dict[str, Any] = {"dsn": dsn}
    if user is not None:
        connect_kwargs["user"] = user
    if password is not None:
        connect_kwargs["password"] = password
    if config_dir is not None:
        connect_kwargs["config_dir"] = config_dir

    with oracledb.connect(**connect_kwargs) as conn, conn.cursor() as cur:
        cur.execute(query)
        fetch_df_all = getattr(cur, "fetch_df_all", None)
        if callable(fetch_df_all):
            df_interface = fetch_df_all()
            if df_interface is None:
                return _empty_arrow_table(cur)
            return _to_arrow(df_interface)
        return _rows_to_arrow(cur)


def sqlalchemy(url: str, query: str, *, params: Mapping[str, Any] | None = None) -> Any:
    """Run `query` via SQLAlchemy and return a `pyarrow.Table`.

    Generic fallback for any database with a SQLAlchemy driver (Postgres,
    MySQL, MSSQL, Oracle via `oracle+oracledb://`, etc.). Less performant than
    a native Arrow-aware driver for very wide or very tall result sets — use
    `oracle()` directly if Oracle is the source.

    Args:
        url: SQLAlchemy URL (e.g. `postgresql+psycopg://user:pw@host/db`).
        query: Raw SQL passed to `text()`.
        params: Optional bind parameters for the query.
    """
    try:
        from sqlalchemy import create_engine, text  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "unwind.connectors.sqlalchemy requires SQLAlchemy; install with "
            "`uv pip install unwind-sql[db]`"
        ) from exc

    engine = create_engine(url)
    with engine.connect() as conn:
        result = conn.execute(text(query), params or {})
        keys = list(result.keys())
        rows = result.fetchall()

    return _build_arrow_table(keys, rows)


def _to_arrow(df_interface: Any) -> Any:
    """Convert any DataFrame-Interchange-Protocol object to a `pyarrow.Table`."""
    import pyarrow as pa  # noqa: PLC0415

    if isinstance(df_interface, pa.Table):
        return df_interface
    interchange = getattr(pa, "interchange", None)
    if interchange is not None and hasattr(interchange, "from_dataframe"):
        return interchange.from_dataframe(df_interface)
    # Last resort: assume it exposes a list-of-columns / list-of-rows API.
    raise TypeError(
        f"could not convert {type(df_interface).__name__} to a pyarrow.Table; "
        "upgrade pyarrow to a version that exposes pyarrow.interchange"
    )


def _rows_to_arrow(cur: Any) -> Any:
    keys = [d[0] for d in cur.description]
    rows = cur.fetchall()
    return _build_arrow_table(keys, rows)


def _build_arrow_table(keys: list[str], rows: Sequence[Any]) -> Any:
    import pyarrow as pa  # noqa: PLC0415

    if not rows:
        return pa.table({k: [] for k in keys})
    columns = {k: [row[i] for row in rows] for i, k in enumerate(keys)}
    return pa.table(columns)


def _empty_arrow_table(cur: Any) -> Any:
    import pyarrow as pa  # noqa: PLC0415

    keys = [d[0] for d in cur.description]
    return pa.table({k: [] for k in keys})
