"""Project, Model, and PythonModel: the core data classes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

if TYPE_CHECKING:
    # `pydantic_ai` is an optional extra and the unwind.{dag,impact,
    # investigator,lineage,runner,trace} modules all import from this file,
    # so these imports live behind TYPE_CHECKING to avoid the circular load.
    from pydantic_ai.models import Model as AIModel

    from unwind._progress import ProgressCallback
    from unwind.dag import DAG
    from unwind.impact import ColumnImpact
    from unwind.investigator import Investigator
    from unwind.lineage import ColumnRef, TableLineage
    from unwind.runner import RunResult
    from unwind.trace import TraceResult


@dataclass(frozen=True, slots=True)
class Model:
    """A single SQL model: a named SQL statement, before and after Jinja rendering.

    `rendered_sql` is `None` until the project has been rendered with concrete
    `vars`. `group`, `tags`, and `materialized` are populated by the loader from
    leading `-- @group:` / `-- @tags:` / `-- @materialized:` directives.

    `materialized` is one of:
        - `"table"` (default): `CREATE OR REPLACE TABLE ... AS (sql)`
        - `"view"`: `CREATE OR REPLACE VIEW ... AS (sql)`
        - `"external"`: `COPY (sql) TO <location> (FORMAT PARQUET)`, then a
          view is created over the resulting parquet so downstream models and
          the web UI keep working.

    `location` (raw) and `rendered_location` (post-Jinja) carry the output
    path for `external` models and are `None` otherwise.

    `origin` is a human-readable identifier of where the model was loaded from
    (e.g. `"file:/abs/path.sql"` or `"db:schema.table#name"`). `path` is set
    only for models loaded from disk.
    """

    name: str
    raw_sql: str
    origin: str
    path: Path | None = None
    rendered_sql: str | None = None
    group: str | None = None
    tags: tuple[str, ...] = ()
    materialized: str = "table"
    location: str | None = None
    rendered_location: str | None = None
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class ModelContext:
    """Runtime handle passed to a Python model's `model(context)` function.

    `connection` is the live DuckDB connection used by the runner. Upstream
    models listed in `depends_on` have already been materialized when the
    function runs, so `context.connection.execute("SELECT * FROM fct_x").arrow()`
    is safe. `variables` are the Jinja vars passed to `Project.run(vars=...)`.

    For the common "this model has one upstream and I want its rows" case,
    use `context.df` — it reads from the single parent as a `pyarrow.Table`
    (lazy: nothing is fetched if the property is not accessed). For
    multi-parent models, `context.dfs[name]` does the same per parent.
    """

    connection: duckdb.DuckDBPyConnection
    variables: Mapping[str, Any]
    project_root: Path | None
    # Names of the model's upstream parents (from PythonModel.depends_on).
    # Used by the `df` / `dfs` lazy loaders below.
    upstreams: tuple[str, ...] = ()

    @property
    def df(self) -> Any:
        """Lazy: the single parent's rows as a `pyarrow.Table`.

        Raises if the model has zero or more than one parent — use
        `context.dfs[name]` instead when there are several.
        """
        if not self.upstreams:
            raise ValueError(
                "context.df requires the model to declare exactly one upstream "
                "in DEPENDS_ON; got none. Use context.connection directly, "
                "or add DEPENDS_ON = ('parent_model_name',)."
            )
        if len(self.upstreams) > 1:
            raise ValueError(
                f"context.df is ambiguous with {len(self.upstreams)} upstreams "
                f"{self.upstreams!r}. Use context.dfs[name] instead."
            )
        return self.connection.execute(
            f'SELECT * FROM "{self.upstreams[0]}"'
        ).to_arrow_table()

    @property
    def dfs(self) -> _LazyUpstreams:
        """Dict-like, lazy: `context.dfs[name]` → parent's Arrow table.

        Iteration and `name in context.dfs` reveal which parents are declared.
        Each lookup re-executes a `SELECT *` (zero-copy on Arrow), so assign
        to a local if you read the same parent multiple times.
        """
        return _LazyUpstreams(self.connection, self.upstreams)


class _LazyUpstreams:
    """Dict-like view over a Python model's upstream parents.

    `__getitem__` runs `SELECT * FROM "<name>"` against the runner's DuckDB
    connection and returns a `pyarrow.Table`. Membership / iteration use the
    declared `DEPENDS_ON` tuple — no fetch.
    """

    __slots__ = ("_conn", "_names")

    def __init__(self, conn: duckdb.DuckDBPyConnection, names: tuple[str, ...]) -> None:
        self._conn = conn
        self._names = names

    def __getitem__(self, name: str) -> Any:
        if name not in self._names:
            raise KeyError(
                f"unknown upstream {name!r}; declared in DEPENDS_ON: {self._names}"
            )
        return self._conn.execute(f'SELECT * FROM "{name}"').to_arrow_table()

    def __iter__(self):
        return iter(self._names)

    def __len__(self) -> int:
        return len(self._names)

    def __contains__(self, name: object) -> bool:
        return name in self._names

    def __repr__(self) -> str:
        return f"<LazyUpstreams names={self._names!r}>"


@dataclass(frozen=True, slots=True)
class PythonModel:
    """A Python-backed model: a function that returns a relation.

    The function signature is `model(context: ModelContext) -> object`. The
    return value can be a `pyarrow.Table`, `pandas.DataFrame`, a
    `duckdb.DuckDBPyRelation`, a raw SQL string (wrapped as
    `CREATE TABLE name AS (...)`), or `None` if the function performed its
    own side-effects via `context.connection`.

    `depends_on` is the explicit list of upstream model names (Python models
    have no SQL, so dependencies cannot be inferred from an AST). The runner
    materializes those upstream models before calling `func`.

    Supported `materialized` values: `"table"` (default) and `"view"`. A
    `"view"` materialization registers the returned relation directly as a
    DuckDB view without copying — useful for large Arrow tables.
    """

    name: str
    func: Callable[[ModelContext], object]
    origin: str
    path: Path | None = None
    depends_on: tuple[str, ...] = ()
    group: str | None = None
    tags: tuple[str, ...] = ()
    materialized: str = "table"
    # `location`/`rendered_location` exist solely to give `ModelOrPython` a
    # union-wide attribute set — consumers can read `m.rendered_location` on
    # any model without isinstance branching, and `ty`'s possibly-missing-
    # attribute checks stay clean. Always `None` for Python models: the
    # `external` materialization is SQL-only.
    location: str | None = None
    rendered_location: str | None = None
    disabled: bool = False


ModelOrPython = Model | PythonModel


@dataclass(slots=True)
class Project:
    """A loaded set of models (SQL or Python), ready to be rendered, planned, and run."""

    models: dict[str, ModelOrPython] = field(default_factory=dict)
    macros: dict[str, str] = field(default_factory=dict)
    root: Path | None = None

    def render(self, variables: Mapping[str, object] | None = None) -> Project:
        """Return a new `Project` with every SQL model rendered."""
        from unwind.renderer import render_project  # noqa: PLC0415

        return render_project(self, variables=variables)

    def dag(self) -> DAG:
        """Build the dependency graph from rendered models."""
        from unwind.dag import build_dag  # noqa: PLC0415

        return build_dag(self)

    def get_table_lineage(
        self, target: str, *, vars: Mapping[str, object] | None = None
    ) -> TableLineage:
        """Return the table-level lineage subgraph rooted at `target`."""
        from unwind.lineage import get_table_lineage  # noqa: PLC0415

        return get_table_lineage(self._ensure_rendered(vars), target)

    def get_column_lineage(
        self,
        target: str,
        *,
        column: str,
        vars: Mapping[str, object] | None = None,
        qualified_sources: dict[str, str] | None = None,
        connection: duckdb.DuckDBPyConnection | None = None,
    ) -> ColumnRef:
        """Return the column-level lineage tree for `target.column`.

        Raises if `target` is a Python model: column lineage requires an
        SQL AST that Python models don't expose. Pass `qualified_sources` (cf.
        `compute_qualified_sources`) or `connection=` (already-materialized
        DuckDB) to skip the per-call materialization pass.
        """
        from unwind.lineage import get_column_lineage  # noqa: PLC0415

        return get_column_lineage(
            self._ensure_rendered(vars),
            target,
            column,
            qualified_sources=qualified_sources,
            connection=connection,
        )

    def get_column_impact(
        self,
        model: str,
        *,
        column: str,
        vars: Mapping[str, object] | None = None,
        connection: duckdb.DuckDBPyConnection | None = None,
        qualified_sources: dict[str, str] | None = None,
    ) -> ColumnImpact:
        """Return the transitive downstream impact of `model.column`.

        Walks the DAG forward and reports every column that would need
        attention if `column` were renamed or retyped. Symmetric to
        `get_column_lineage` but in the opposite direction. Pass `connection=`
        to reuse a DuckDB connection that already holds the materialized DAG;
        pass `qualified_sources=` to skip the per-call sqlglot qualify pass.
        """
        from unwind.impact import get_column_impact  # noqa: PLC0415

        return get_column_impact(
            self._ensure_rendered(vars),
            model,
            column,
            connection=connection,
            qualified_sources=qualified_sources,
        )

    def _ensure_rendered(self, variables: Mapping[str, object] | None) -> Project:
        if all(
            isinstance(m, PythonModel) or m.rendered_sql is not None
            for m in self.models.values()
        ):
            return self
        return self.render(variables)

    def trace_value(
        self,
        *,
        model: str,
        column: str,
        where: Mapping[str, object],
        depth: int | None = None,
        max_values: int | None = 5,
        vars: Mapping[str, object] | None = None,
        connection: duckdb.DuckDBPyConnection | None = None,
        qualified_sources: dict[str, str] | None = None,
    ) -> TraceResult:
        """Trace `(model.column, where)` back to the source values that contributed.

        Pass `connection=` to reuse a DuckDB connection that already holds the
        materialized DAG; this skips the per-call materialization pass.
        Pass `qualified_sources=` (cf. `unwind.lineage.compute_qualified_sources`)
        to also skip the per-call sqlglot qualify pass.
        """
        from unwind.trace import trace_value  # noqa: PLC0415

        return trace_value(
            self._ensure_rendered(vars),
            model=model,
            column=column,
            where=where,
            depth=depth,
            max_values=max_values,
            connection=connection,
            qualified_sources=qualified_sources,
        )

    def get_investigator(
        self,
        *,
        llm_provider: str = "openai",
        model: str | AIModel | None = None,
        language: str = "en",
    ) -> Investigator:
        """Return an LLM `Investigator` that turns a `TraceResult` into prose."""
        from unwind.investigator import get_investigator  # noqa: PLC0415

        return get_investigator(llm_provider=llm_provider, model=model, language=language)

    def show(
        self,
        *,
        vars: Mapping[str, object] | None = None,
        host: str = "127.0.0.1",
        port: int = 8765,
        open_browser: bool = True,
    ) -> None:
        """Launch a web UI to navigate the DAG and column lineage. Blocks until Ctrl+C."""
        from unwind.web import serve  # noqa: PLC0415

        serve(
            self._ensure_rendered(vars),
            host=host,
            port=port,
            open_browser=open_browser,
        )

    def run(
        self,
        *,
        vars: Mapping[str, object] | None = None,
        target: str | None = None,
        database: str | Path = ":memory:",
        connection: duckdb.DuckDBPyConnection | None = None,
        debug: bool = False,
        workers: int = 1,
        on_event: ProgressCallback | None = None,
    ) -> RunResult:
        """Render, plan, and execute the project on DuckDB.

        Pass `connection=` to reuse an existing `DuckDBPyConnection` (e.g. one
        with extensions installed or secrets configured). When `connection` is
        given, `database` is ignored and the connection is left open — the
        caller owns it. Otherwise Unwind opens, uses, and closes its own
        connection to `database`.

        Pass `workers=N` (default `1`) to materialize independent models in
        parallel via a `ThreadPoolExecutor` with one `conn.cursor()` per
        worker. DuckDB serializes DDL at the engine layer; Python-model code
        runs in worker threads. With `workers=1` execution stays on the
        calling thread — bit-for-bit identical to pre-parallel behaviour.

        Progress: when `on_event` is `None`, the runner installs a default
        live progress UI iff stderr is a TTY, `rich` is importable, and
        `UNWIND_NO_PROGRESS` is unset. Pass a custom `on_event` callback to
        observe events without rendering, or `lambda _: None` to fully mute.
        """
        from unwind.runner import run_project  # noqa: PLC0415

        return run_project(
            self,
            variables=vars,
            target=target,
            database=database,
            connection=connection,
            debug=debug,
            workers=workers,
            on_event=on_event,
        )
