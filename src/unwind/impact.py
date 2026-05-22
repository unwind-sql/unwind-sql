"""Forward column lineage: who is affected if I rename / retype this column?

Symmetric to `unwind.lineage` (which walks *upstream* — "where does this value
come from?"), this module walks *downstream* — "where does this value go?" —
to support change-impact analysis.

For each SQL model downstream of the source column we look at **all** column
references in the rendered AST, not just the SELECT projection, because a
rename of a JOIN key or a WHERE column also breaks the downstream even
though sqlglot's lineage walker would never report a value-propagation edge
for it. Each reference is classified by the clause it sits in (projection,
join, filter, group, order). Only `projection` references propagate transitively
— that's the lineage edge that carries the value into a new column.

Python models are opaque: we can't introspect their `model(context)` body, so
any Python model that lists an impacted SQL model in its `DEPENDS_ON` shows
up under `opaque_consumers`. The caller decides what to do with that list
(typically: open the model file and audit it by hand).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import Node as SqlglotNode
from sqlglot.lineage import lineage as sqlglot_lineage

from unwind._sql import DIALECT
from unwind.errors import UnwindError
from unwind.project import Project, PythonModel
from unwind.runner import _materialize_disabled, _quote_ident, materialize_model
from unwind.trace import _column_types, _qualify


class ImpactError(UnwindError):
    """Raised when impact analysis cannot be computed (unknown model/column, parse error)."""


Usage = Literal["projection", "join", "filter", "group", "order"]


@dataclass(frozen=True, slots=True)
class ImpactedColumn:
    """One column transitively affected by the source change."""

    model: str
    column: str
    column_type: str
    # SQL fragment computing this column from its inputs. Best-effort — empty
    # when sqlglot can't isolate a single projection expression.
    expression: str


@dataclass(frozen=True, slots=True)
class ImpactEdge:
    """One direct usage of `parent_model.parent_column` by `child_model`.

    `child_column` is the downstream output column for `usage == "projection"`
    (the value propagates into that column); it is `None` for join / filter /
    group / order usages, where the upstream column is referenced but doesn't
    flow into a single named output.
    """

    parent_model: str
    parent_column: str
    child_model: str
    child_column: str | None
    usage: Usage


@dataclass(frozen=True, slots=True)
class ColumnImpact:
    """Result of `get_column_impact`."""

    source_model: str
    source_column: str
    source_type: str
    affected: tuple[ImpactedColumn, ...]
    edges: tuple[ImpactEdge, ...]
    # Python models that depend on an affected (model, column) pair. We can't
    # introspect their bodies, so they're flagged here for manual review.
    opaque_consumers: tuple[str, ...]


def get_column_impact(
    project: Project,
    model: str,
    column: str,
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
    qualified_sources: dict[str, str] | None = None,
) -> ColumnImpact:
    """Compute the transitive downstream impact of `model.column`.

    Args:
        project: A loaded (and ideally rendered) project. Will auto-render if
            needed.
        model: Source model name.
        column: Source column name (case-insensitive).
        connection: An existing DuckDB connection holding the materialized
            DAG. When supplied, the in-function materialization pass is
            skipped — the single biggest cost on large DAGs.
        qualified_sources: Pre-computed `{model: qualified_sql}` (cf.
            `compute_qualified_sources`). When supplied, skips the per-call
            sqlglot parse + qualify pass over every SQL model.

    Raises:
        ImpactError: if `model` or `column` is unknown, or if a downstream
            model fails to parse.
    """
    if model not in project.models:
        raise ImpactError(f"unknown model: {model!r}")
    if isinstance(project.models[model], PythonModel):
        # We could in principle still report impact starting from a Python
        # model's output — its columns surface as opaque leaves to sqlglot.
        # Accept and proceed; the BFS below works the same way.
        pass

    rendered = project if _all_rendered(project) else project.render()

    if connection is not None:
        return _impact_on(rendered, connection, model, column, qualified_sources)

    conn = duckdb.connect(":memory:")
    try:
        dag = rendered.dag()
        for name in dag.execution_order:
            mdl = rendered.models[name]
            if mdl.disabled:
                parents = sorted(dag.nodes[name].depends_on_models)
                _materialize_disabled(conn, name, parents, debug=False)
                continue
            materialize_model(
                conn,
                mdl,
                variables={},
                project_root=rendered.root,
                respect_external=False,
            )
        return _impact_on(rendered, conn, model, column, qualified_sources)
    finally:
        conn.close()


def _impact_on(
    rendered: Project,
    conn: duckdb.DuckDBPyConnection,
    model: str,
    column: str,
    qualified_sources: dict[str, str] | None,
) -> ColumnImpact:
    # Resolve source column to its canonical (case-correct) name and type.
    src_cols = _columns_actual(conn, model)
    if column.lower() not in src_cols:
        raise ImpactError(f"unknown column: {model}.{column}")
    canonical_source_column = src_cols[column.lower()]
    source_type = _type_of(conn, model, canonical_source_column)

    # Per-model qualified SQL — qualify() expands `t.*` so the lineage walker
    # sees every output column explicitly. Reuse caller-supplied cache when
    # available to skip the sqlglot parse+qualify pass per model.
    if qualified_sources is not None:
        qualified = {
            name: q
            for name, q in qualified_sources.items()
            if name in rendered.models
        }
    else:
        schema = {name: _column_types(conn, name) for name in rendered.models}
        qualified = {
            name: _qualify(m.rendered_sql, schema)
            for name, m in rendered.models.items()
            if not isinstance(m, PythonModel) and m.rendered_sql is not None
        }

    dag = rendered.dag()
    direct_children: dict[str, list[str]] = {}
    for child_name, node in dag.nodes.items():
        for parent in node.depends_on_models:
            direct_children.setdefault(parent.lower(), []).append(child_name)

    affected, edges, opaque = _bfs_impact(
        rendered=rendered,
        conn=conn,
        qualified=qualified,
        direct_children=direct_children,
        source_model=model,
        source_column=canonical_source_column,
    )

    return ColumnImpact(
        source_model=model,
        source_column=canonical_source_column,
        source_type=source_type,
        affected=tuple(affected),
        edges=tuple(edges),
        opaque_consumers=tuple(sorted(opaque)),
    )


def _bfs_impact(
    *,
    rendered: Project,
    conn: duckdb.DuckDBPyConnection,
    qualified: dict[str, str],
    direct_children: dict[str, list[str]],
    source_model: str,
    source_column: str,
) -> tuple[list[ImpactedColumn], list[ImpactEdge], set[str]]:
    """BFS from `(source_model, source_column)` through the column-flow graph.

    Pulled out of `get_column_impact` so the public entry point stays
    readable. The traversal only follows `projection` edges — that's the
    only relation that propagates a *value*. Non-projection edges (join,
    filter, group, order) are recorded once per `(parent, child, usage)`
    triple and never re-entered.
    """
    affected: list[ImpactedColumn] = []
    edges: list[ImpactEdge] = []
    opaque: set[str] = set()
    seen_columns: set[tuple[str, str]] = {
        (source_model.lower(), source_column.lower())
    }
    seen_edges: set[tuple[str, str, str, str | None, str]] = set()
    queue: list[tuple[str, str]] = [(source_model, source_column)]

    while queue:
        cur_model, cur_col = queue.pop()
        for child_name in direct_children.get(cur_model.lower(), []):
            child = rendered.models[child_name]
            if isinstance(child, PythonModel):
                opaque.add(child_name)
                continue
            child_sql = qualified.get(child_name)
            if child_sql is None:
                continue

            usages = _find_usages(child_sql, cur_model, cur_col)
            if not usages:
                continue

            if "projection" in usages:
                _record_projection(
                    conn=conn,
                    qualified=qualified,
                    child_name=child_name,
                    child_sql=child_sql,
                    cur_model=cur_model,
                    cur_col=cur_col,
                    affected=affected,
                    edges=edges,
                    seen_columns=seen_columns,
                    seen_edges=seen_edges,
                    queue=queue,
                )

            for usage in usages - {"projection"}:
                edge_key = (
                    cur_model.lower(),
                    cur_col.lower(),
                    child_name.lower(),
                    None,
                    usage,
                )
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                edges.append(
                    ImpactEdge(
                        parent_model=cur_model,
                        parent_column=cur_col,
                        child_model=child_name,
                        child_column=None,
                        usage=usage,  # type: ignore[arg-type]
                    )
                )

    return affected, edges, opaque


def _record_projection(
    *,
    conn: duckdb.DuckDBPyConnection,
    qualified: dict[str, str],
    child_name: str,
    child_sql: str,
    cur_model: str,
    cur_col: str,
    affected: list[ImpactedColumn],
    edges: list[ImpactEdge],
    seen_columns: set[tuple[str, str]],
    seen_edges: set[tuple[str, str, str, str | None, str]],
    queue: list[tuple[str, str]],
) -> None:
    """Walk every output column of `child_name` that descends from `(cur_model, cur_col)`."""
    for out_col in _projection_outputs(
        child_sql, qualified, child_name, cur_model, cur_col
    ):
        edge_key = (
            cur_model.lower(),
            cur_col.lower(),
            child_name.lower(),
            out_col.lower(),
            "projection",
        )
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)
        edges.append(
            ImpactEdge(
                parent_model=cur_model,
                parent_column=cur_col,
                child_model=child_name,
                child_column=out_col,
                usage="projection",
            )
        )
        node_key = (child_name.lower(), out_col.lower())
        if node_key in seen_columns:
            continue
        seen_columns.add(node_key)
        affected.append(
            ImpactedColumn(
                model=child_name,
                column=out_col,
                column_type=_type_of(conn, child_name, out_col),
                expression=_projection_expression(child_sql, out_col),
            )
        )
        queue.append((child_name, out_col))


def _all_rendered(project: Project) -> bool:
    return all(
        isinstance(m, PythonModel) or m.rendered_sql is not None
        for m in project.models.values()
    )


def _columns_actual(conn: duckdb.DuckDBPyConnection, model: str) -> dict[str, str]:
    """`{lowercase_name: actual_name}` for `model` via DuckDB DESCRIBE."""
    rows = conn.execute(f"DESCRIBE {_quote_ident(model)}").fetchall()
    return {str(r[0]).lower(): str(r[0]) for r in rows}


def _type_of(conn: duckdb.DuckDBPyConnection, model: str, column: str) -> str:
    rows = conn.execute(f"DESCRIBE {_quote_ident(model)}").fetchall()
    for r in rows:
        if str(r[0]).lower() == column.lower():
            return str(r[1])
    return "UNKNOWN"


def _find_usages(
    qualified_sql: str, source_model: str, source_column: str
) -> set[Usage]:
    """Return the set of clauses in which `source_model.source_column` is referenced.

    Works on the *qualified* SQL (output of `_qualify`) so every column is
    table-qualified and `t.*` projections are already expanded — no
    unresolved-alias ambiguity.
    """
    try:
        tree = sqlglot.parse_one(qualified_sql, dialect=DIALECT)
    except SqlglotError:
        return set()

    src_model_l = source_model.lower()
    src_col_l = source_column.lower()

    # Map alias → underlying table name (lowercased) for the FROM/JOIN scope.
    alias_to_table: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        name = (table.name or "").lower()
        alias = (table.alias or table.name or "").lower()
        if alias:
            alias_to_table[alias] = name

    usages: set[Usage] = set()
    for col in tree.find_all(exp.Column):
        if (col.name or "").lower() != src_col_l:
            continue
        qualifier = (col.table or "").lower()
        if not qualifier:
            # Qualified SQL should have a table on every column; if not we
            # can't safely attribute this reference.
            continue
        resolved = alias_to_table.get(qualifier, qualifier)
        if resolved != src_model_l:
            continue
        usages.add(_classify_usage(col))
    return usages


def _classify_usage(col: exp.Column) -> Usage:
    """Classify a column reference by the nearest clause-defining ancestor."""
    node = col.parent
    while node is not None:
        if isinstance(node, exp.Join):
            return "join"
        if isinstance(node, (exp.Where, exp.Having)):
            return "filter"
        if isinstance(node, exp.Group):
            return "group"
        if isinstance(node, exp.Order):
            return "order"
        node = node.parent
    return "projection"


def _projection_outputs(
    child_sql: str,
    qualified_sources: dict[str, str],
    child_name: str,
    source_model: str,
    source_column: str,
) -> list[str]:
    """Output columns of `child_name` whose lineage transitively reads from `source`."""
    try:
        tree = sqlglot.parse_one(child_sql, dialect=DIALECT)
    except SqlglotError:
        return []
    select = tree.find(exp.Select)
    if select is None:
        return []

    # The *projection columns* of the outermost SELECT — that's the set of
    # output columns we can ask sqlglot's lineage walker about.
    output_columns: list[str] = []
    for expr in select.expressions:
        alias = _projection_alias(expr)
        if alias is not None:
            output_columns.append(alias)

    other_sources = {n: q for n, q in qualified_sources.items() if n != child_name}
    src_model_l = source_model.lower()
    src_col_l = source_column.lower()

    affected: list[str] = []
    for col in output_columns:
        try:
            root = sqlglot_lineage(col, sql=child_sql, sources=other_sources, dialect=DIALECT)
        except SqlglotError:
            continue
        if _lineage_contains(root, src_model_l, src_col_l):
            affected.append(col)
    return affected


def _projection_alias(expr: exp.Expression) -> str | None:
    if isinstance(expr, exp.Alias):
        return expr.alias
    if isinstance(expr, exp.Column):
        return expr.name
    name = expr.alias_or_name
    return name or None


def _lineage_contains(node: SqlglotNode, target_model: str, target_column: str) -> bool:
    """True if some leaf in the lineage tree refers to `target_model.target_column`.

    When sqlglot reaches an opaque source (a Python model, or anything not in
    `sources`) the leaf carries the original FROM-clause expression rather
    than a resolved `source_name`. We mirror `trace._extract_ref` here and
    fall back to the expression's table name in that case.
    """
    raw_name = node.name or ""
    raw_col = raw_name.split(".", 1)[1] if "." in raw_name else raw_name
    col = raw_col.strip('"').lower()

    if node.source_name:
        if node.source_name.lower() == target_model and col == target_column:
            return True
    else:
        table = _extract_table_name(node.expression)
        if table is not None and table.lower() == target_model and col == target_column:
            return True

    return any(
        _lineage_contains(child, target_model, target_column)
        for child in node.downstream
    )


def _extract_table_name(expr: object) -> str | None:
    """Return the underlying table name if `expr` is a Table reference (or aliased one)."""
    candidate = expr
    if isinstance(candidate, exp.Alias):
        candidate = candidate.this
    if isinstance(candidate, exp.Table):
        return candidate.name
    return None


def _projection_expression(qualified_sql: str, output_column: str) -> str:
    """Best-effort SQL fragment for `output_column` in the outermost SELECT."""
    try:
        tree = sqlglot.parse_one(qualified_sql, dialect=DIALECT)
    except SqlglotError:
        return ""
    select = tree.find(exp.Select)
    if select is None:
        return ""
    target = output_column.lower().strip('"')
    for expr in select.expressions:
        alias = _projection_alias(expr)
        if alias is None:
            continue
        if alias.lower().strip('"') == target:
            body = expr.this if isinstance(expr, exp.Alias) else expr
            return body.sql(dialect=DIALECT)
    return ""
