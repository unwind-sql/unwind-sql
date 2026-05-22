"""Static lineage at the table and column level.

`TableLineage` is derived from the DAG: it lists every model upstream of a
target and the edges between them.

`ColumnRef` is built by `sqlglot.lineage.lineage`, which walks the AST through
CTEs, joins, and registered upstream sources to expose the column expression
and its transitive upstream columns.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
from sqlglot.errors import SqlglotError
from sqlglot.lineage import Node as SqlglotNode
from sqlglot.lineage import lineage as sqlglot_lineage

from unwind._sql import DIALECT
from unwind.dag import build_dag
from unwind.errors import UnwindError
from unwind.project import Project, PythonModel


class LineageError(UnwindError):
    """Raised when lineage cannot be computed (unknown model/column, parse error)."""


@dataclass(frozen=True, slots=True)
class TableLineage:
    """Subgraph rooted at `target`, containing only its transitive upstream."""

    target: str
    nodes: frozenset[str]
    edges: frozenset[tuple[str, str]]


@dataclass(frozen=True, slots=True)
class ColumnRef:
    """A column expression and the upstream columns it depends on.

    `name` is the qualified column reference as reported by sqlglot (typically
    `MODEL.COLUMN`). `expression` is the SQL fragment that produces the value.
    """

    name: str
    expression: str
    upstream: tuple[ColumnRef, ...]


def get_table_lineage(project: Project, target: str) -> TableLineage:
    """Return the table lineage subgraph rooted at `target` (inclusive)."""
    dag = build_dag(project)
    if target not in dag.nodes:
        raise LineageError(f"unknown model: {target!r}")

    keep = dag.upstream(target, include_self=True)
    edges = frozenset(
        (parent, node.name)
        for node in dag.nodes.values()
        if node.name in keep
        for parent in node.depends_on_models
    )
    return TableLineage(target=target, nodes=keep, edges=edges)


def compute_qualified_sources(
    project: Project,
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> dict[str, str]:
    """Materialize the project once and return `{model_name: qualified_sql}`.

    The qualified SQL has every `SELECT t.*` rewritten to its explicit column
    list — required for sqlglot's lineage walker to resolve columns hidden
    behind `*`. Computing this is expensive (it spins up a DuckDB instance
    and runs the whole DAG), so callers that want to compute many lineages
    over the same project should call this **once** and pass the result to
    `get_column_lineage(..., qualified_sources=...)`.

    Pass `connection=` to reuse a DuckDB connection that already has every
    model materialized — skips the in-function materialization pass.
    """
    from unwind.trace import _column_types, _materialize, _qualify  # noqa: PLC0415

    for name, model in project.models.items():
        if isinstance(model, PythonModel):
            continue
        if model.rendered_sql is None:
            raise LineageError(
                f"upstream model {name!r} is not rendered; call Project.render(...) first"
            )

    if connection is not None:
        schema_dict = {name: _column_types(connection, name) for name in project.models}
    else:
        conn = duckdb.connect(":memory:")
        try:
            _materialize(project, conn)
            schema_dict = {name: _column_types(conn, name) for name in project.models}
        finally:
            conn.close()

    result: dict[str, str] = {}
    for name, model in project.models.items():
        if isinstance(model, PythonModel):
            continue
        assert model.rendered_sql is not None  # validated above
        result[name] = _qualify(model.rendered_sql, schema_dict)
    return result


def get_column_lineage(
    project: Project,
    target: str,
    column: str,
    *,
    qualified_sources: dict[str, str] | None = None,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> ColumnRef:
    """Trace the lineage of `target.column` through every upstream model.

    Pass a pre-computed `qualified_sources` (cf. `compute_qualified_sources`)
    to skip the per-call materialization — useful when scanning many columns
    or when a long-lived process can cache the schema. Alternatively pass
    `connection=` to reuse an already-materialized DuckDB.
    """
    if target not in project.models:
        raise LineageError(f"unknown model: {target!r}")
    target_model = project.models[target]
    if isinstance(target_model, PythonModel):
        raise LineageError(
            f"column lineage is not available for Python model {target!r}: "
            "no SQL AST to walk. Query a downstream SQL model instead."
        )
    if target_model.rendered_sql is None:
        raise LineageError(f"model {target!r} is not rendered; call Project.render(...) first")

    if qualified_sources is None:
        qualified_sources = compute_qualified_sources(project, connection=connection)

    target_sql = qualified_sources[target]
    # Restrict the source set passed to sqlglot to the transitive upstream
    # of `target` — sqlglot parses every entry in `sources`, so trimming
    # unrelated models down-to-depth is a large speedup on wide DAGs.
    upstream = build_dag(project).upstream(target, include_self=False)
    sources = {n: qualified_sources[n] for n in upstream if n in qualified_sources}

    try:
        root = sqlglot_lineage(
            column,
            sql=target_sql,
            sources=sources,
            dialect=DIALECT,
        )
    except SqlglotError as exc:
        raise LineageError(f"column lineage failed for {target}.{column}: {exc}") from exc

    return _convert(root)


def _convert(node: SqlglotNode) -> ColumnRef:
    return ColumnRef(
        name=node.name or "?",
        expression=node.expression.sql(dialect=DIALECT),
        upstream=tuple(_convert(d) for d in node.downstream),
    )
