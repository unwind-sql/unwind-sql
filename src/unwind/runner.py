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
effects via `context.connection`.

The runner owns its DuckDB connection (in-memory unless a path is provided)
and returns a `RunResult` that records every executed model with its row
count and wall-clock duration.

Concurrency: `workers > 1` runs sibling models (those whose dependencies are
all satisfied) in parallel via a `ThreadPoolExecutor` + one `conn.cursor()`
per worker. DuckDB serializes DDL at the database level, so two workers
materializing different tables on the same connection is safe. With the
default `workers=1`, execution stays on the calling thread — identical to
the pre-parallel behaviour.

Failure model: any error during a model's execution is wrapped in `RunError`.
In parallel mode, the first failure cancels not-yet-started tasks; in-flight
tasks finish naturally before the run aborts.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import closing
from dataclasses import dataclass, field
from graphlib import TopologicalSorter
from pathlib import Path
from typing import Any

import duckdb

from unwind._progress import EventKind, ProgressCallback, RunEvent, auto_progress
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
    connection: duckdb.DuckDBPyConnection | None = None,
    debug: bool = False,
    workers: int = 1,
    on_event: ProgressCallback | None = None,
) -> RunResult:
    """Render `project`, build its DAG, and materialize models on DuckDB.

    Args:
        project: A loaded (not necessarily rendered) project.
        variables: Jinja vars passed through to the renderer and to Python
            models via `ModelContext.variables`.
        target: If set, only `target` and its transitive upstream are run.
        database: DuckDB database location. Defaults to in-memory. Ignored
            when `connection` is provided.
        connection: An existing `DuckDBPyConnection` to materialize into. The
            caller retains ownership — the connection is not closed.
        debug: If True, print each model's SQL and timing to stdout.
        workers: Maximum number of models materialized in parallel. The
            default `1` runs on the calling thread (no `ThreadPoolExecutor`
            overhead, identical to pre-parallel behaviour). With `workers>1`,
            DuckDB cursors are created per worker; DDL is serialized by the
            engine, so concurrent materialization of *distinct* tables is
            safe. Python-model code runs in worker threads — beware of
            non-thread-safe libraries.
        on_event: Optional progress observer. Receives a `RunEvent` at every
            scheduling boundary (run start, model start, model done, model
            skipped, run done). When `None` (default), the runner tries
            `auto_progress()` — a rich-based live UI gated on TTY + the
            optional `[progress]` extra. Pass `lambda _: None` to silence
            even when a TTY/rich combo would otherwise opt in.

    Raises:
        RunError: if any model fails on DuckDB.
        DAGError: if the project cannot be planned.
        TemplateRenderError: if rendering fails.
        ValueError: if `workers < 1`.
    """
    if workers < 1:
        raise ValueError(f"workers must be >= 1, got {workers}")

    rendered = project.render(variables)
    dag = rendered.dag()
    if target is not None:
        dag = dag.subdag(target)

    resolved_on_event = auto_progress() if on_event is None else on_event

    if connection is not None:
        return _execute(
            rendered, dag, connection,
            variables=variables or {}, debug=debug, workers=workers, on_event=resolved_on_event,
        )
    with closing(duckdb.connect(str(database))) as conn:
        return _execute(
            rendered, dag, conn,
            variables=variables or {}, debug=debug, workers=workers, on_event=resolved_on_event,
        )


def _execute(
    project: Project,
    dag: DAG,
    conn: duckdb.DuckDBPyConnection,
    *,
    variables: Mapping[str, Any],
    debug: bool,
    workers: int,
    on_event: ProgressCallback | None,
) -> RunResult:
    """Drive the topological scheduler, emit events, collect `ExecutedModel`s.

    `workers == 1` stays on the calling thread (no `ThreadPoolExecutor`
    overhead, simpler tracebacks); `workers > 1` opens a pool and dispatches
    ready nodes onto per-worker `conn.cursor()`s.
    """
    total = len(dag.nodes)
    executed: list[ExecutedModel] = []
    run_start = time.perf_counter()

    def emit(
        kind: EventKind,
        *,
        name: str | None = None,
        in_flight: tuple[str, ...] = (),
        duration_s: float | None = None,
        row_count: int | None = None,
    ) -> None:
        if on_event is None:
            return
        on_event(RunEvent(
            kind=kind,
            name=name,
            completed=len(executed),
            total=total,
            in_flight=in_flight,
            duration_s=duration_s,
            row_count=row_count,
            elapsed_s=time.perf_counter() - run_start,
        ))

    emit("start")

    if workers == 1:
        _execute_sequential(
            project, dag, conn,
            variables=variables, debug=debug, executed=executed, emit=emit,
        )
    else:
        _execute_parallel(
            project, dag, conn,
            variables=variables, debug=debug, executed=executed,
            workers=workers, emit=emit,
        )

    emit("done")
    return RunResult(executed=executed, total_duration_s=time.perf_counter() - run_start)


def _execute_sequential(
    project: Project,
    dag: DAG,
    conn: duckdb.DuckDBPyConnection,
    *,
    variables: Mapping[str, Any],
    debug: bool,
    executed: list[ExecutedModel],
    emit: Any,
) -> None:
    """Single-threaded topological execution — the pre-parallel code path."""
    for name in dag.execution_order:
        emit("model_start", name=name, in_flight=(name,))
        outcome = _run_one_model(
            project, dag, conn, name, variables=variables, debug=debug,
        )
        if outcome is None:
            emit("model_skipped", name=name)
            continue
        executed.append(outcome)
        emit(
            "model_done",
            name=name,
            duration_s=outcome.duration_s,
            row_count=outcome.row_count,
        )


def _execute_parallel(
    project: Project,
    dag: DAG,
    conn: duckdb.DuckDBPyConnection,
    *,
    variables: Mapping[str, Any],
    debug: bool,
    executed: list[ExecutedModel],
    workers: int,
    emit: Any,
) -> None:
    """`graphlib.TopologicalSorter`-driven parallel execution.

    Each ready node is submitted to a `ThreadPoolExecutor` with its own
    `conn.cursor()` so DDL on distinct tables doesn't serialize through a
    single cursor's transaction state. The first `RunError` cancels every
    not-yet-started future; already-running ones finish naturally before
    the run aborts.
    """
    ts: TopologicalSorter[str] = TopologicalSorter()
    for node in dag.nodes.values():
        ts.add(node.name, *node.depends_on_models)
    ts.prepare()

    in_flight: dict[Future[ExecutedModel | None], str] = {}
    pending_error: RunError | None = None

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="unwind") as pool:
        while ts.is_active():
            if pending_error is None:
                for name in ts.get_ready():
                    cursor = conn.cursor()
                    fut = pool.submit(
                        _run_one_model,
                        project, dag, cursor, name,
                        variables=variables, debug=debug,
                    )
                    in_flight[fut] = name
                    emit("model_start", name=name, in_flight=tuple(sorted(in_flight.values())))
            if not in_flight:
                # No ready nodes and nothing in flight while ts.is_active() means
                # the sorter is awaiting `done(name)` calls — defensively break.
                break
            done_futures, _ = wait(list(in_flight), return_when=FIRST_COMPLETED)
            for fut in done_futures:
                name = in_flight.pop(fut)
                try:
                    outcome = fut.result()
                except RunError as exc:
                    pending_error = pending_error or exc
                    # Mark the node done so the sorter can drain pending tasks;
                    # we won't submit new ones once `pending_error` is set.
                    ts.done(name)
                    continue
                ts.done(name)
                still_in_flight = tuple(sorted(in_flight.values()))
                if outcome is None:
                    emit("model_skipped", name=name, in_flight=still_in_flight)
                    continue
                executed.append(outcome)
                emit(
                    "model_done",
                    name=name,
                    in_flight=still_in_flight,
                    duration_s=outcome.duration_s,
                    row_count=outcome.row_count,
                )

    if pending_error is not None:
        raise pending_error


def _run_one_model(
    project: Project,
    dag: DAG,
    conn: duckdb.DuckDBPyConnection,
    name: str,
    *,
    variables: Mapping[str, Any],
    debug: bool,
) -> ExecutedModel | None:
    """Materialize one model and report its row count + duration.

    Returns `None` for a disabled leaf that had nothing to alias — the
    runner records this as "skipped", not "executed". Any failure inside
    DuckDB or a Python model is wrapped in `RunError`.
    """
    model = project.models[name]
    parents = sorted(dag.nodes[name].depends_on_models)
    model_start = time.perf_counter()

    if model.disabled:
        try:
            kind_label = _materialize_disabled(conn, name, parents, debug=debug)
        except duckdb.Error as exc:
            raise RunError(name, str(exc)) from exc
        if kind_label is None:
            if debug:
                print(f"-- {name}: skipped (disabled, no parents)")
            return None
        try:
            row = conn.execute(
                f"SELECT COUNT(*) FROM {_quote_ident(name)}"
            ).fetchone()
        except duckdb.Error as exc:
            raise RunError(name, str(exc)) from exc
        assert row is not None, "COUNT(*) always returns a row"
        rows = int(row[0])
        duration = time.perf_counter() - model_start
        if debug:
            print(f"-- {name}: {rows} rows in {duration * 1000:.1f} ms ({kind_label})")
        return ExecutedModel(name=name, row_count=rows, duration_s=duration)

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
    if debug:
        print(f"-- {name}: {rows} rows in {duration * 1000:.1f} ms ({kind_label})")
    return ExecutedModel(name=name, row_count=rows, duration_s=duration)


def materialize_model(
    conn: duckdb.DuckDBPyConnection,
    model: ModelOrPython,
    *,
    variables: Mapping[str, Any],
    project_root: Path | None,
    respect_external: bool,
    view_only: bool = False,
    debug: bool = False,
) -> str:
    """Materialize one model into `conn` and return its `kind_label`.

    Used by the runner, by `trace`, and by the web app's bootstrap. Set
    `respect_external=False` to coerce `external` SQL models into plain
    tables — useful when callers only need the data in DuckDB and don't
    want to write parquet files. Set `view_only=True` to force every model
    (SQL and Python) to materialize as a VIEW, regardless of its declared
    `@materialized`; this lets the web UI boot lazily on huge DAGs by
    deferring actual computation until queries land.
    """
    if isinstance(model, PythonModel):
        return _materialize_python(
            conn,
            model,
            variables=variables,
            project_root=project_root,
            view_only=view_only,
            debug=debug,
        )
    return _materialize_sql(
        conn,
        model,
        respect_external=respect_external,
        view_only=view_only,
        debug=debug,
    )


def _materialize_disabled(
    conn: duckdb.DuckDBPyConnection,
    name: str,
    parents: list[str],
    *,
    debug: bool,
) -> str | None:
    """Materialize a disabled model as a view aliasing its first parent.

    Blender-style mute: skip the body entirely and forward the first parent's
    rows under this model's name so children referencing it keep working.
    Returns the `kind_label` used by the runner, or `None` when the model has
    no parents (in which case we leave nothing materialized — downstream
    references will surface a clear "Table not found" at run time).
    """
    if not parents:
        return None
    alias = parents[0]
    statement = (
        f"CREATE OR REPLACE VIEW {_quote_ident(name)} AS "
        f"SELECT * FROM {_quote_ident(alias)}"
    )
    if debug:
        print(f"-- {name} (disabled -> aliasing {alias})\n{statement}")
    conn.execute(statement)
    return "disabled"


def _materialize_sql(
    conn: duckdb.DuckDBPyConnection,
    model: Model,
    *,
    respect_external: bool,
    view_only: bool,
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

    kind = "VIEW" if view_only or model.materialized == "view" else "TABLE"
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
    view_only: bool,
    debug: bool,
) -> str:
    context = ModelContext(
        connection=conn,
        variables=variables,
        project_root=project_root,
        upstreams=model.depends_on,
    )
    if debug:
        print(f"-- {model.name} (python {model.materialized})")
    result = model.func(context)
    name = model.name

    if result is None:
        # The function used `context.connection` to materialize itself. DDL
        # via `execute("CREATE TABLE ...")` is catalog-persistent and visible
        # across cursors; `register()` side-effects are not, so a `None`
        # return paired with `.register()` will not survive a parallel run.
        if not _relation_exists(conn, name):
            raise ValueError(
                f"Python model {name!r} returned None and did not register "
                f"a relation named {name!r} on the connection"
            )
        return f"python-{model.materialized}"

    if isinstance(result, str):
        body = result.rstrip().rstrip(";")
        kind = "VIEW" if view_only else "TABLE"
        conn.execute(f"CREATE OR REPLACE {kind} {_quote_ident(name)} AS ({body})")
        return f"python-{kind.lower()}"

    # Coerce to a `DuckDBPyRelation` and use `.create_view()` / `.create()`
    # to materialize. The older pattern (`conn.register(tmp, result)` +
    # `CREATE VIEW name AS SELECT * FROM tmp`) made the view reference a
    # cursor-local registration that other workers could not resolve — fine
    # in sequential mode, broken under `workers > 1`. The relation API binds
    # the data inside the catalog entry, so the resulting view/table is
    # cross-cursor visible and survives GC of the original Python object.
    rel = _as_relation(conn, result)
    if view_only or model.materialized == "view":
        rel.create_view(name, replace=True)
    else:
        # `DuckDBPyRelation.create()` has no replace= kwarg; pre-drop is the
        # documented idiom for idempotent table materialization from a rel.
        conn.execute(f"DROP TABLE IF EXISTS {_quote_ident(name)}")
        conn.execute(f"DROP VIEW IF EXISTS {_quote_ident(name)}")
        rel.create(name)
    return f"python-{model.materialized}"


def _as_relation(
    conn: duckdb.DuckDBPyConnection, result: object
) -> duckdb.DuckDBPyRelation:
    """Coerce a Python model's return value into a `DuckDBPyRelation`.

    Duck-types `pyarrow.Table` and `pandas.DataFrame` rather than importing
    them (both are optional user deps; unwind doesn't ship them).
    """
    if isinstance(result, duckdb.DuckDBPyRelation):
        return result
    if hasattr(result, "schema") and hasattr(result, "num_rows"):
        return conn.from_arrow(result)  # type: ignore[arg-type]
    cls = type(result)
    if cls.__module__.startswith("pandas") and cls.__name__ == "DataFrame":
        return conn.from_df(result)  # type: ignore[arg-type]
    raise ValueError(
        f"unsupported Python model return type {cls.__name__!r}; "
        "return a pyarrow.Table, pandas.DataFrame, DuckDBPyRelation, "
        "raw SQL string, or None (with side-effects via context.connection)"
    )


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
