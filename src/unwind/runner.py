"""DuckDB runner: materializes a DAG's models in topological order.

A SQL model's persistence depends on its `@materialized:` directive:

    table     (default): `CREATE OR REPLACE TABLE <name> AS (sql)`
    view              : `CREATE OR REPLACE VIEW <name> AS (sql)`
    external          : `COPY (sql) TO '<location>' (FORMAT PARQUET)`, followed
                        by `CREATE OR REPLACE VIEW <name> AS read_parquet(...)`
                        so downstream references and the web UI keep working.

A Python model is materialized by calling its `model(context)` function. The
return value is registered with DuckDB (zero-copy for Arrow), then promoted
to a `TABLE` (default) or stays as a registered relation when `MATERIALIZED
= "view"`. A `None` return value means the function handled its own side
effects via `context.duckdb`.

The runner owns its DuckDB connection (in-memory unless a path is provided)
and returns a `RunResult` that records every executed model with its row
count and wall-clock duration.

Failure model: any error during a model's execution is wrapped in `RunError`
and aborts the run — downstream models are not attempted.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from unwind.dag import DAG
from unwind.errors import UnwindError
from unwind.project import (
    Model,
    ModelContext,
    ModelOrPython,
    Project,
    PythonModel,
)


class RunError(UnwindError):
    """Raised when a model fails to execute on the engine."""

    def __init__(self, model_name: str, message: str) -> None:
        super().__init__(f"failed to run model {model_name!r}: {message}")
        self.model_name = model_name


@dataclass(frozen=True, slots=True)
class ExecutedModel:
    """One materialized model: its name, row count, and execution time."""

    name: str
    row_count: int
    duration_s: float


@dataclass(slots=True)
class RunResult:
    """Outcome of a successful `Project.run` call."""

    executed: list[ExecutedModel] = field(default_factory=list)
    total_duration_s: float = 0.0

    @property
    def names(self) -> list[str]:
        return [m.name for m in self.executed]


def run_project(
    project: Project,
    *,
    variables: Mapping[str, object] | None = None,
    target: str | None = None,
    database: str | Path = ":memory:",
    debug: bool = False,
) -> RunResult:
    """Render `project`, build its DAG, and materialize models on DuckDB.

    Args:
        project: A loaded (not necessarily rendered) project.
        variables: Jinja vars passed through to the renderer and to Python
            models via `ModelContext.variables`.
        target: If set, only `target` and its transitive upstream are run.
        database: DuckDB database location. Defaults to in-memory.
        debug: If True, print each model's SQL and timing to stdout.

    Raises:
        RunError: if any model fails on DuckDB.
        DAGError: if the project cannot be planned.
        TemplateRenderError: if rendering fails.
    """
    rendered = project.render(variables)
    dag = rendered.dag()
    if target is not None:
        dag = dag.subdag(target)

    with closing(duckdb.connect(str(database))) as conn:
        return _execute(rendered, dag, conn, variables=variables or {}, debug=debug)


def _execute(
    project: Project,
    dag: DAG,
    conn: duckdb.DuckDBPyConnection,
    *,
    variables: Mapping[str, Any],
    debug: bool,
) -> RunResult:
    executed: list[ExecutedModel] = []
    run_start = time.perf_counter()

    for name in dag.execution_order:
        model = project.models[name]
        model_start = time.perf_counter()
        try:
            kind_label = materialize_model(
                conn,
                model,
                variables=variables,
                project_root=project.root,
                respect_external=True,
                debug=debug,
            )
            row = conn.execute(f"SELECT COUNT(*) FROM {_quote_ident(name)}").fetchone()
        except (duckdb.Error, ValueError) as exc:
            raise RunError(name, str(exc)) from exc
        except Exception as exc:
            raise RunError(name, f"{type(exc).__name__}: {exc}") from exc

        assert row is not None, "COUNT(*) always returns a row"
        rows = int(row[0])
        duration = time.perf_counter() - model_start
        executed.append(ExecutedModel(name=name, row_count=rows, duration_s=duration))
        if debug:
            print(f"-- {name}: {rows} rows in {duration * 1000:.1f} ms ({kind_label})")

    return RunResult(executed=executed, total_duration_s=time.perf_counter() - run_start)


def materialize_model(
    conn: duckdb.DuckDBPyConnection,
    model: ModelOrPython,
    *,
    variables: Mapping[str, Any],
    project_root: Path | None,
    respect_external: bool,
    debug: bool = False,
) -> str:
    """Materialize one model into `conn` and return its `kind_label`.

    Used by the runner, by `trace`, and by the web app's bootstrap. Set
    `respect_external=False` to coerce `external` SQL models into plain
    tables — useful when callers only need the data in DuckDB and don't
    want to write parquet files.
    """
    if isinstance(model, PythonModel):
        return _materialize_python(
            conn, model, variables=variables, project_root=project_root, debug=debug
        )
    return _materialize_sql(conn, model, respect_external=respect_external, debug=debug)


def _materialize_sql(
    conn: duckdb.DuckDBPyConnection,
    model: Model,
    *,
    respect_external: bool,
    debug: bool,
) -> str:
    sql = model.rendered_sql
    assert sql is not None, "renderer must populate rendered_sql before run"
    body = sql.rstrip().rstrip(";")
    name = model.name

    if model.materialized == "external" and respect_external:
        location = model.rendered_location
        assert location is not None, "external models must have a rendered_location"
        Path(location).parent.mkdir(parents=True, exist_ok=True)
        escaped = location.replace("'", "''")
        copy_stmt = f"COPY ({body}) TO '{escaped}' (FORMAT PARQUET)"
        view_stmt = (
            f"CREATE OR REPLACE VIEW {_quote_ident(name)} AS "
            f"SELECT * FROM read_parquet('{escaped}')"
        )
        if debug:
            print(f"-- {name} (external -> {location})\n{copy_stmt}\n{view_stmt}")
        conn.execute(copy_stmt)
        conn.execute(view_stmt)
        return "external"

    kind = "VIEW" if model.materialized == "view" else "TABLE"
    statement = f"CREATE OR REPLACE {kind} {_quote_ident(name)} AS ({body})"
    if debug:
        print(f"-- {name} ({kind.lower()})\n{statement}")
    conn.execute(statement)
    return kind.lower()


def _materialize_python(
    conn: duckdb.DuckDBPyConnection,
    model: PythonModel,
    *,
    variables: Mapping[str, Any],
    project_root: Path | None,
    debug: bool,
) -> str:
    context = ModelContext(duckdb=conn, variables=variables, project_root=project_root)
    if debug:
        print(f"-- {model.name} (python {model.materialized})")
    result = model.func(context)
    name = model.name

    if result is None:
        # The function used `context.duckdb` to register what it wanted.
        if not _relation_exists(conn, name):
            raise ValueError(
                f"Python model {name!r} returned None and did not register "
                f"a relation named {name!r} on the connection"
            )
        return f"python-{model.materialized}"

    if isinstance(result, str):
        body = result.rstrip().rstrip(";")
        conn.execute(f"CREATE OR REPLACE TABLE {_quote_ident(name)} AS ({body})")
        return "python-table"

    tmp_name = f"__py_src_{name}"
    conn.register(tmp_name, result)
    try:
        if model.materialized == "view":
            conn.execute(
                f"CREATE OR REPLACE VIEW {_quote_ident(name)} AS "
                f"SELECT * FROM {_quote_ident(tmp_name)}"
            )
        else:
            conn.execute(
                f"CREATE OR REPLACE TABLE {_quote_ident(name)} AS "
                f"SELECT * FROM {_quote_ident(tmp_name)}"
            )
    finally:
        # A VIEW keeps a reference to the registered Arrow/relation; a TABLE
        # has already copied the data so we can unregister. Be conservative
        # and keep it registered either way — DuckDB's relation cache is cheap.
        pass
    return f"python-{model.materialized}"


def _relation_exists(conn: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM duckdb_tables() WHERE lower(table_name) = lower(?) "
        "UNION ALL "
        "SELECT 1 FROM duckdb_views() WHERE lower(view_name) = lower(?) LIMIT 1",
        [name, name],
    ).fetchone()
    return row is not None


def _quote_ident(name: str) -> str:
    """Quote a DuckDB identifier safely. Doubles embedded quotes."""
    escaped = name.replace('"', '""')
    return f'"{escaped}"'
