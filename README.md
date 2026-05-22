# unwind

A DuckDB-only SQL/Python orchestrator with Jinja templating, a semantic layer,
and lineage at the table, column, and value level. An alternative to Dbt and
SQLMesh, focused on deterministic value-lineage backed by a SQLGlot AST and
DuckDB as the single execution engine.

A project is a directory of `.sql` and `.py` files. SQL models give full
table/column/value lineage via the SQLGlot AST. Python models are opaque to
lineage but participate in the DAG like any other node — typical use cases
are **sources** (you bring your own Postgres/Oracle/S3/CSV ingestion code)
and **sinks** (exporting a final table elsewhere). The two kinds compose
freely.

Unwind deliberately ships zero DB-third-party dependencies. If you want to
pull from Postgres, you `pip install psycopg` yourself; if you want Oracle,
you `pip install oracledb`. Unwind's only DB is DuckDB.

> Status: **alpha** — table, column, and deterministic value lineage all work
> end-to-end, plus a web UI and an optional LLM investigator (pydantic-ai,
> multi-provider).

## Install

The core install (`duckdb`, `jinja2`, `sqlglot`) is enough to load a project,
plan its DAG, run it on DuckDB, and compute table / column / value lineage in
Python. The two surfaces that reach beyond the core — the web UI and the LLM
investigator — live behind optional extras.

| Extra | What it enables                                               | Pulls in                       |
| ----- | ------------------------------------------------------------- | ------------------------------ |
| `web` | `Project.show()` (FastAPI/Uvicorn DAG explorer)               | `fastapi`, `uvicorn[standard]` |
| `llm` | `Project.get_investigator()` (multi-provider via pydantic-ai) | `pydantic-ai`                  |
| `all` | Both of the above, no decisions required                      | sum of the two extras          |

Pick what you need:

```bash
pip install unwind-sql                    # core only
pip install "unwind-sql[web]"             # add the web UI
pip install "unwind-sql[web,llm]"         # web UI + LLM investigator
pip install "unwind-sql[all]"             # everything — recommended for trying it out
```

For local development on this repo:

```bash
uv sync    # core + the dev group (which includes web/llm bits and pyarrow)
```

## Run the example

The bundled example computes net margin per order over 5 raw/ref tables, with
a Jinja macro and a final aggregation by warehouse:

```bash
cd example/
uv run python generate_data.py   # one-time: write the 5 parquet sources
uv run python main.py
```

The script:

1. Loads `models/` (mixed `.sql` + `.py`) and runs the full DAG on DuckDB.
2. Prints the table & column lineage of `int_tax_costs.local_tax_fee`.
3. Traces `local_tax_fee` for `order_id="ORD-7892"` back to the raw values
   that contributed (`raw_orders.gross_sales = 500.0`, `ref_local_taxes.tax_pct
= 0.20`, …).
4. If `OPENAI_API_KEY` is set, asks an LLM to explain the trace in plain
   language and flag suspicious values (uses `pydantic-ai`, swap providers
   by passing `llm_provider="anthropic"` etc.).
5. Opens a browser tab on the **web UI** (Cytoscape.js DAG + per-column
   lineage tree). Press `Ctrl+C` to stop.

`raw_orders` in the example is a **Python model** ([example/models/raw_orders.py](example/models/raw_orders.py))
that reads the bundled parquet fixture via `pyarrow`. To wire it to your own
source (Postgres, Oracle, S3, REST API, …) edit `example/models/helpers.py` —
Unwind has no built-in connectors, so you import the lib you want and call it
yourself.

See [example/main.py](example/main.py) and [example/README.md](example/README.md)
for the model walkthrough.

## Python models

A file in `models/` whose Python module defines a top-level callable
`model(context)` is recognised as a node of the DAG. Anything else in
`models/*.py` is imported as a plain helper module — so `from helpers
import load_data` just works.

```python
# models/raw_orders.py — Arrow-native ingestion, zero-copy into DuckDB
import pyarrow.parquet as pq

GROUP = "costs"
MATERIALIZED = "view"     # "table" (default) or "view"
DEPENDS_ON = ()           # tuple of upstream model names

def model(context):
    # context.connection (live DuckDBPyConnection),
    # context.variables (Jinja vars), context.project_root (path passed to load()).
    return pq.read_table("data/raw_orders.parquet")
```

The return value is registered into DuckDB (zero-copy for Arrow tables and
DuckDB relations). Returning a `str` runs it as SQL; returning `None` means
the function did its own work via `context.connection`.

Want to ingest from Postgres? Install `psycopg` yourself and let DuckDB pull
it natively:

```python
def model(context):
    context.connection.execute("INSTALL postgres; LOAD postgres;")
    context.connection.execute(
        "CREATE OR REPLACE TABLE raw_orders AS "
        "SELECT * FROM postgres_scan('host=… dbname=…', 'public', 'orders')"
    )
```

## Loading SQL definitions from somewhere else

Models usually live in `.sql` files, but you can also feed them in directly
from any source you can fetch yourself — a metadata table, a YAML registry,
an HTTP endpoint. Unwind doesn't connect to anything; you bring the rows.

```python
import unwind

rows = [
    {"name": "stg_users", "sql": "SELECT id, email FROM raw_users", "kind": "model"},
    {"name": "plus_one",  "sql": "{% macro plus_one(c) %}{{c}}+1{% endmacro %}", "kind": "macro"},
]
project = unwind.load_from_rows(rows, origin="catalog.sql_defs")
```

## Reusing an existing DuckDB connection

`Project.run()` opens a fresh in-memory DuckDB by default, but you can pass
your own connection — useful when you've already installed extensions,
attached external databases, or configured secrets:

```python
import duckdb, unwind

conn = duckdb.connect(":memory:")
conn.execute("INSTALL httpfs; LOAD httpfs;")
unwind.load("models/").run(connection=conn)
conn.execute("SELECT * FROM fct_warehouse_profitability").fetchall()  # still open
```

## Test

```bash
uv run pytest
uv run ruff check
uv run ty check
```
