# unwind

SQL orchestration with Jinja templating, a semantic layer, and lineage at the
table, column, and value level. An alternative to Dbt and SQLMesh, focused on
deterministic value-lineage backed by a SQLGlot AST and a DuckDB execution
engine.

A project is a directory of `.sql` and `.py` files. SQL models give full
table/column/value lineage via the SQLGlot AST. Python models are opaque to
lineage but participate in the DAG like any other node — typical use cases
are sources (loading data from Oracle / Postgres / Parquet) and sinks
(exporting a final table elsewhere). The two kinds compose freely.

> Status: **alpha** — table, column, and deterministic value lineage all work
> end-to-end, plus a web UI and an optional LLM investigator (pydantic-ai,
> multi-provider).

## Install

The core install (`duckdb`, `jinja2`, `sqlglot`) is enough to load a project,
plan its DAG, run it, and compute table / column / value lineage in Python.
Anything that talks to the outside world — the web UI, the LLM investigator,
the source connectors, the SQL-stored-in-DB loader — lives behind an
optional extra so you only install what you actually use.

| Extra        | What it enables                                           | Pulls in                          |
| ------------ | --------------------------------------------------------- | --------------------------------- |
| `web`        | `Project.show()` (FastAPI/Uvicorn DAG explorer)           | `fastapi`, `uvicorn[standard]`    |
| `llm`        | `Project.get_investigator()` (multi-provider via pydantic-ai) | `pydantic-ai`                |
| `db`         | `unwind.load_from_db()` + `connectors.sqlalchemy(...)`    | `sqlalchemy`                      |
| `connectors` | `unwind.connectors.parquet/oracle/...` for Python models  | `pyarrow`, `oracledb`             |
| `all`        | Everything above, no decisions required                   | sum of the four extras            |

Pick what you need:

```bash
pip install unwind-sql                    # core only
pip install "unwind-sql[web]"             # add the web UI
pip install "unwind-sql[web,llm]"         # web UI + LLM investigator
pip install "unwind-sql[connectors]"      # Python-model loaders (parquet, oracle, …)
pip install "unwind-sql[all]"             # everything — recommended for trying it out
```

For local development on this repo:

```bash
uv sync --all-extras    # equivalent to the `[all]` extra
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
backed by a one-line `load_data()` helper. Switch its backend without touching
any SQL:

```bash
UNWIND_SOURCE_MODE=parquet   uv run python main.py   # default
UNWIND_SOURCE_MODE=oracle    uv run python main.py   # needs oracledb + ORACLE_DSN
```

See [example/main.py](example/main.py) and [example/README.md](example/README.md)
for the model walkthrough.

## Python models

A file in `models/` whose Python module defines a top-level callable
`model(context)` is recognised as a node of the DAG. Anything else in
`models/*.py` is imported as a plain helper module — so `from helpers
import load_data` just works.

```python
# models/raw_orders.py
from helpers import load_data

GROUP = "costs"
MATERIALIZED = "view"     # "table" (default) or "view"
DEPENDS_ON = ()           # tuple of upstream model names

def model(context):
    # context.duckdb (live connection), context.variables (Jinja vars),
    # context.project_root (the path passed to unwind.load)
    return load_data("raw_orders")  # pyarrow.Table, DataFrame, str (SQL), or None
```

The return value is registered into DuckDB (zero-copy for Arrow). For a
sink, do the work via `context.duckdb` and `return None`.

`unwind.connectors` ships tiny helpers (`parquet`, `oracle`, `sqlalchemy`) so
your `load_data()` reduces to a few lines of branching.

## Test

```bash
uv run pytest
uv run ruff check
uv run ty check
```
