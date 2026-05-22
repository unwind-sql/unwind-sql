"""Deterministic value lineage: trace a cell back to its source values.

Given `(model, column, where)`, walks the SQLGlot column-lineage tree and, at
every node, reads the actual values from the materialized DuckDB tables.

For a node `(M, C)` we apply the user's `where` to the *most appropriate*
table:

- If every predicate column exists in `M`, filter `M` directly.
- Otherwise fall back to the target model — the projection that already
  joined the upstream rows. This makes `ref_*` tables (joined via a key not
  in `where`, e.g. `warehouse_id`) work without manual join translation: the
  target row already contains the propagated columns.

`TraceError` is raised when the predicate cannot be resolved at the target.
Empty `values` at a deeper node signal "value not propagated to target and
predicate doesn't apply directly" — uncommon, but possible for derived columns
that never reach the target.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import Node as SqlglotNode
from sqlglot.lineage import lineage as sqlglot_lineage
from sqlglot.optimizer.qualify import qualify

from unwind._sql import DIALECT
from unwind.errors import UnwindError
from unwind.project import Project, PythonModel
from unwind.runner import _materialize_disabled, _quote_ident, materialize_model


class TraceError(UnwindError):
    """Raised when value lineage cannot be computed."""


DEFAULT_MAX_VALUES = 5


@dataclass(frozen=True, slots=True)
class TraceNode:
    """A column reference along with the values that flowed through it.

    `expression` is the SQL formula that computes this node's value (column
    references intact). `substituted` is the same formula with the immediate
    upstream column references replaced by their concrete values; for
    multi-row contributions (typical of aggregates) the list is truncated
    to `max_values` items in the substituted form.

    `values` is bounded to at most `max_values + 1` items by the SQL fetch
    so aggregates over large tables stay cheap. `value_count` carries the
    real number of contributing rows; when `value_count > len(values)`, the
    fetch was truncated and the substituted form shows `...+N`.
    """

    model: str
    column: str
    expression: str
    substituted: str
    values: tuple[Any, ...]
    value_count: int
    predicate: dict[str, Any]
    upstream: tuple[TraceNode, ...]


@dataclass(frozen=True, slots=True)
class TraceResult:
    """A `TraceNode` tree rooted at the target cell."""

    model: str
    column: str
    where: dict[str, Any]
    root: TraceNode


def trace_value(
    project: Project,
    *,
    model: str,
    column: str,
    where: Mapping[str, Any],
    depth: int | None = None,
    max_values: int | None = DEFAULT_MAX_VALUES,
    connection: duckdb.DuckDBPyConnection | None = None,
    qualified_sources: dict[str, str] | None = None,
) -> TraceResult:
    """Trace a cell `(model.column, where)` to its source values.

    Pass `connection=` to reuse a DuckDB connection that already has every
    model materialized (e.g. the web app's bootstrap connection). When given,
    `trace_value` skips its own materialization step entirely — the single
    biggest cost on large DAGs.

    Pass `qualified_sources=` (from `compute_qualified_sources`) to skip the
    per-call sqlglot parse + `qualify` pass over every SQL model — the next
    biggest cost once materialization is shared.
    """
    if not where:
        raise TraceError("`where` must contain at least one column/value")
    if model not in project.models:
        raise TraceError(f"unknown model: {model!r}")
    if isinstance(project.models[model], PythonModel):
        raise TraceError(
            f"cannot trace through Python model {model!r}: value lineage "
            "requires SQL. Pick a downstream SQL model as the target instead."
        )

    rendered = project if _is_rendered(project) else project.render()

    if connection is not None:
        return _trace_on(
            rendered, connection, model, column, where, depth, max_values,
            qualified_sources=qualified_sources,
        )

    conn = duckdb.connect(":memory:")
    try:
        _materialize(rendered, conn)
        return _trace_on(
            rendered, conn, model, column, where, depth, max_values,
            qualified_sources=qualified_sources,
        )
    finally:
        conn.close()


def _trace_on(
    rendered: Project,
    conn: duckdb.DuckDBPyConnection,
    model: str,
    column: str,
    where: Mapping[str, Any],
    depth: int | None,
    max_values: int | None,
    *,
    qualified_sources: dict[str, str] | None,
) -> TraceResult:
    target_sql, sources = _resolve_qualified(rendered, conn, model, qualified_sources)
    try:
        sg_root = sqlglot_lineage(
            column, sql=target_sql, sources=sources, dialect=DIALECT
        )
    except SqlglotError as exc:
        raise TraceError(f"column lineage failed for {model}.{column}: {exc}") from exc

    normalized_where = _normalize_predicate(conn, model, dict(where))
    root = _build_node(
        sg_root,
        conn,
        target_model=model,
        target_predicate=normalized_where,
        depth=depth,
        max_values=max_values,
    )
    return TraceResult(model=model, column=column, where=normalized_where, root=root)


def _resolve_qualified(
    rendered: Project,
    conn: duckdb.DuckDBPyConnection,
    model: str,
    qualified_sources: dict[str, str] | None,
) -> tuple[str, dict[str, str]]:
    """Return `(target_sql, sources)` ready to hand to `sqlglot_lineage`.

    Uses caller-supplied `qualified_sources` when present (skipping a sqlglot
    parse+qualify pass per model — the dominant cost on wide DAGs). Otherwise
    builds a fresh schema dict from DuckDB and qualifies every SQL model.

    `sources` always excludes `model` (sqlglot wants the target as `sql=`,
    not as a source entry) and any Python or unrendered model.
    """
    if qualified_sources is not None:
        target_sql = qualified_sources.get(model)
        if target_sql is None:
            # Caller-supplied cache is missing the target; fall through to a
            # full rebuild rather than silently returning a broken lineage.
            qualified_sources = None
    if qualified_sources is None:
        # `qualify` needs a {table: {col: type}} schema to expand `t.*`.
        schema_dict = {name: _column_types(conn, name) for name in rendered.models}
        target_model_obj = rendered.models[model]
        assert not isinstance(target_model_obj, PythonModel)  # checked by caller
        raw_target = target_model_obj.rendered_sql
        assert raw_target is not None
        target_sql = _qualify(raw_target, schema_dict)
        sources = {
            n: _qualify(m.rendered_sql, schema_dict)
            for n, m in rendered.models.items()
            if n != model
            and not isinstance(m, PythonModel)
            and m.rendered_sql is not None
        }
        return target_sql, sources

    assert target_sql is not None  # set above when qualified_sources hit
    sources = {n: q for n, q in qualified_sources.items() if n != model}
    return target_sql, sources


def _qualify(sql: str, schema: dict[str, dict[str, str]]) -> str:
    """Pre-expand `t.*` and qualify column references using DuckDB's schema.

    sqlglot's lineage walker can't resolve a column hidden behind `SELECT *`
    when the surrounding SELECT doesn't name it explicitly. Running through
    `qualify` ahead of time rewrites `t.*` into the explicit column list,
    which lets lineage traverse the chain. Falls back to the original SQL on
    any sqlglot error so that exotic constructs we don't support don't break
    the trace altogether.
    """
    try:
        parsed = sqlglot.parse_one(sql, dialect=DIALECT)
        # sqlglot.qualify wants `dict[str, object]`; our schema is the more
        # specific `dict[str, dict[str, str]]` (a valid `object`) — widen.
        widened: dict[str, object] = dict(schema)
        qualified = qualify(parsed, schema=widened, dialect=DIALECT)
        return qualified.sql(dialect=DIALECT)
    except SqlglotError:
        return sql


def _is_rendered(project: Project) -> bool:
    return all(
        isinstance(m, PythonModel) or m.rendered_sql is not None
        for m in project.models.values()
    )


def _materialize(project: Project, conn: duckdb.DuckDBPyConnection) -> None:
    dag = project.dag()
    for name in dag.execution_order:
        model = project.models[name]
        if model.disabled:
            parents = sorted(dag.nodes[name].depends_on_models)
            _materialize_disabled(conn, name, parents, debug=False)
            continue
        materialize_model(
            conn,
            model,
            variables={},
            project_root=project.root,
            # trace doesn't write parquets — coerce external models into plain
            # tables so the data is available for value-lineage queries.
            respect_external=False,
        )


def _columns(conn: duckdb.DuckDBPyConnection, model: str) -> dict[str, str]:
    """Return `{lowercase_name: actual_name}` for `model`."""
    rows = conn.execute(f"DESCRIBE {_quote_ident(model)}").fetchall()
    return {str(r[0]).lower(): str(r[0]) for r in rows}


def _column_types(conn: duckdb.DuckDBPyConnection, model: str) -> dict[str, str]:
    """Return `{actual_name: duckdb_type}` for `model`."""
    rows = conn.execute(f"DESCRIBE {_quote_ident(model)}").fetchall()
    return {str(r[0]): str(r[1]) for r in rows}


def _normalize_predicate(
    conn: duckdb.DuckDBPyConnection, model: str, where: dict[str, Any]
) -> dict[str, Any]:
    cols = _columns(conn, model)
    out: dict[str, Any] = {}
    missing: list[str] = []
    for key, value in where.items():
        canonical = cols.get(key.lower())
        if canonical is None:
            missing.append(key)
        else:
            out[canonical] = value
    if missing:
        raise TraceError(f"predicate columns not in {model!r}: {missing}")
    return out


def _build_node(
    sg_node: SqlglotNode,
    conn: duckdb.DuckDBPyConnection,
    *,
    target_model: str,
    target_predicate: dict[str, Any],
    depth: int | None,
    max_values: int | None,
) -> TraceNode:
    model, column = _extract_ref(sg_node, target_model)
    expr_ast = _normalize_expression(sg_node.expression, column)
    expression = expr_ast.sql(dialect=DIALECT)
    values, value_count, used_predicate = _fetch_values(
        conn, model, column, target_model, target_predicate, max_values=max_values
    )

    if depth is not None and depth <= 0:
        upstream: tuple[TraceNode, ...] = ()
    else:
        next_depth = None if depth is None else depth - 1
        upstream = tuple(
            _build_node(
                child,
                conn,
                target_model=target_model,
                target_predicate=target_predicate,
                depth=next_depth,
                max_values=max_values,
            )
            for child in sg_node.downstream
            if not _is_terminal_star(child)
        )

    substituted = _substitute_ast(
        expr_ast,
        upstream=upstream,
        self_model=model,
        self_column=column,
        self_values=values,
        self_count=value_count,
        max_values=max_values,
    )

    return TraceNode(
        model=model,
        column=column,
        expression=expression,
        substituted=substituted,
        values=values,
        value_count=value_count,
        predicate=used_predicate,
        upstream=upstream,
    )


def _normalize_expression(expr: exp.Expr, column: str) -> exp.Expr:
    """Strip SELECT-list noise from a lineage node's expression.

    Removes the outer `AS alias`, drops user comments, and rewrites bare
    `*` or `Table` references (from opaque sources sqlglot couldn't recurse
    into — Python models or `SELECT * FROM read_parquet(...)`) as a plain
    column reference so the substituted form shows the actual value.
    """
    cleaned = expr.copy()

    def _strip_comments(node: exp.Expr) -> exp.Expr:
        if node.comments:
            node.comments = None
        return node

    cleaned = cleaned.transform(_strip_comments)
    if isinstance(cleaned, exp.Alias):
        cleaned = cleaned.this
    if isinstance(cleaned, (exp.Star, exp.Table)):
        cleaned = exp.column(column)
    return cleaned


def _extract_ref(sg_node: SqlglotNode, target_model: str) -> tuple[str, str]:
    """Resolve the (model, column) pair for a sqlglot lineage node.

    Resolution order:

    1. `source_name` — set by sqlglot when the column comes from a registered
       `sources` entry. Most reliable.
    2. If the node's expression is a Table reference (or an alias of one),
       use that table's name. Triggered when the lineage walk hits a source
       sqlglot couldn't recurse into — typically a Python model treated as
       an opaque leaf.
    3. Fall back to a `prefix.column` split on the node's name, stripping
       SQL identifier quotes.
    4. Fall back to the target model.
    """
    name = sg_node.name or ""
    raw_column = name.split(".", 1)[1] if "." in name else name
    column = _strip_quotes(raw_column).lower()

    if sg_node.source_name:
        return sg_node.source_name.lower(), column

    table_name = _extract_table_name(sg_node.expression)
    if table_name is not None:
        return table_name.lower(), column

    if "." in name:
        prefix, _ = name.split(".", 1)
        return _strip_quotes(prefix).lower(), column

    return target_model.lower(), column


def _extract_table_name(expr: exp.Expr) -> str | None:
    """Return the underlying table name if `expr` is a Table reference."""
    candidate: exp.Expr = expr
    if isinstance(candidate, exp.Alias):
        candidate = candidate.this
    if isinstance(candidate, exp.Table):
        return candidate.name
    return None


def _strip_quotes(token: str) -> str:
    """Drop surrounding double-quotes that sqlglot's qualifier added."""
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1]
    return token


def _is_terminal_star(sg_node: SqlglotNode) -> bool:
    """`*` leaf produced by `SELECT * FROM read_parquet(...)` — not a useful node."""
    return (sg_node.name or "") == "*" and not sg_node.source_name


def _fetch_values(
    conn: duckdb.DuckDBPyConnection,
    model: str,
    column: str,
    target_model: str,
    target_predicate: dict[str, Any],
    *,
    max_values: int | None,
) -> tuple[tuple[Any, ...], int, dict[str, Any]]:
    if not _table_exists(conn, model):
        return (), 0, {}

    model_cols = _columns(conn, model)
    if column.lower() not in model_cols:
        # Column missing here — try the target's denormalized projection.
        return _fallback_via_target(
            conn, target_model, column, target_predicate, max_values=max_values
        )
    column_actual = model_cols[column.lower()]

    applicable = {k: v for k, v in target_predicate.items() if k.lower() in model_cols}
    if applicable:
        values, total = _query(
            conn, model, column_actual, applicable, distinct=False, max_values=max_values
        )
        return values, total, dict(applicable)
    return _fallback_via_target(
        conn, target_model, column, target_predicate, max_values=max_values
    )


def _fallback_via_target(
    conn: duckdb.DuckDBPyConnection,
    target_model: str,
    column: str,
    target_predicate: dict[str, Any],
    *,
    max_values: int | None,
) -> tuple[tuple[Any, ...], int, dict[str, Any]]:
    target_cols = _columns(conn, target_model)
    if column.lower() not in target_cols:
        return (), 0, {}
    actual = target_cols[column.lower()]
    values, total = _query(
        conn, target_model, actual, target_predicate, distinct=True, max_values=max_values
    )
    return values, total, dict(target_predicate)


def _table_exists(conn: duckdb.DuckDBPyConnection, model: str) -> bool:
    """True if `model` is queryable as either a TABLE or a VIEW on `conn`."""
    row = conn.execute(
        "SELECT 1 FROM duckdb_tables() WHERE lower(table_name) = lower(?) "
        "UNION ALL "
        "SELECT 1 FROM duckdb_views() WHERE lower(view_name) = lower(?) "
        "LIMIT 1",
        [model, model],
    ).fetchone()
    return row is not None


def _query(
    conn: duckdb.DuckDBPyConnection,
    model: str,
    column: str,
    predicate: dict[str, Any],
    *,
    distinct: bool,
    max_values: int | None,
) -> tuple[tuple[Any, ...], int]:
    """Return (values, total_count). When `max_values` is set, the row fetch is
    capped at `max_values + 1`; the true count comes from a separate COUNT to
    keep substitution overflow accurate without scanning every contributing row.

    Predicate values are wrapped in `TRY_CAST(? AS <coltype>)` so that types
    that don't actually match the bound column degrade to a NULL comparison
    (no match) instead of raising a ConversionException. This matters when
    the same column name carries different types across upstream models, or
    when source parquet files have surprising types (e.g. an all-NULL column
    inferred as INTEGER instead of VARCHAR).
    """
    types = _column_types(conn, model)
    # `IS NOT DISTINCT FROM` so NULL matches NULL (the cell-click predicate
    # often carries NULL columns from the user's row); TRY_CAST so a value of
    # the wrong type collapses to NULL on its side instead of raising
    # ConversionException.
    parts = [
        f"{_quote_ident(k)} IS NOT DISTINCT FROM TRY_CAST(? AS {types.get(k, 'VARCHAR')})"
        for k in predicate
    ]
    where_sql = " AND ".join(parts)
    head = "SELECT DISTINCT" if distinct else "SELECT"
    select_sql = f"{head} {_quote_ident(column)} FROM {_quote_ident(model)} WHERE {where_sql}"
    params = list(predicate.values())

    if max_values is None:
        rows = conn.execute(select_sql, params).fetchall()
        values = tuple(r[0] for r in rows)
        return values, len(values)

    count_expr = (
        f"COUNT(DISTINCT {_quote_ident(column)})" if distinct else "COUNT(*)"
    )
    count_sql = f"SELECT {count_expr} FROM {_quote_ident(model)} WHERE {where_sql}"
    count_row = conn.execute(count_sql, params).fetchone()
    total = 0 if count_row is None or count_row[0] is None else int(count_row[0])

    rows = conn.execute(f"{select_sql} LIMIT ?", [*params, max_values + 1]).fetchall()
    values = tuple(r[0] for r in rows)
    return values, total


def _substitute_ast(
    expr: exp.Expr,
    *,
    upstream: tuple[TraceNode, ...],
    self_model: str,
    self_column: str,
    self_values: tuple[Any, ...],
    self_count: int,
    max_values: int | None,
) -> str:
    """Replace column references in `expr` with concrete values, returning SQL.

    Upstream values win over self-values; both are looked up by `(model, column)`,
    falling back to `(any, column)` for unqualified references. Each entry carries
    `(values, total_count)` so the overflow indicator stays accurate even when
    `values` was capped by SQL `LIMIT`. Multi-value cells are emitted as
    `[v1, v2, ...+N]` (not strictly valid SQL but readable).
    """
    qualified: dict[tuple[str, str], tuple[tuple[Any, ...], int]] = {}
    unqualified: dict[str, tuple[tuple[Any, ...], int]] = {}
    # seed with self so unqualified leaf references substitute to the cell's value
    qualified[(self_model.lower(), self_column.lower())] = (self_values, self_count)
    unqualified.setdefault(self_column.lower(), (self_values, self_count))
    for node in upstream:
        qualified[(node.model.lower(), node.column.lower())] = (node.values, node.value_count)
        unqualified.setdefault(node.column.lower(), (node.values, node.value_count))

    def transform(node: exp.Expr) -> exp.Expr:
        if not isinstance(node, exp.Column):
            return node
        col = node.name.lower()
        table = (node.table or "").lower()
        entry: tuple[tuple[Any, ...], int] | None = None
        if table:
            entry = qualified.get((table, col))
        if entry is None:
            entry = unqualified.get(col)
        if entry is None:
            return node
        values, count = entry
        return _values_to_expr(values, count, max_values)

    return expr.copy().transform(transform).sql(dialect=DIALECT)


def _values_to_expr(values: tuple[Any, ...], total_count: int, max_values: int | None) -> exp.Expr:
    if not values:
        return exp.Null()
    if total_count == 1:
        return _scalar_to_literal(values[0])

    truncated = list(values) if max_values is None else list(values[:max_values])
    overflow = 0 if max_values is None else max(0, total_count - max_values)
    parts = [_format_scalar(v) for v in truncated]
    if overflow > 0:
        parts.append(f"...+{overflow}")
    return exp.Var(this=f"[{', '.join(parts)}]")


def _scalar_to_literal(value: Any) -> exp.Expr:
    if value is None:
        return exp.Null()
    if isinstance(value, bool):  # before int — bool is a subclass of int
        return exp.Boolean(this=value)
    if isinstance(value, (int, float)):
        return exp.Literal.number(value)
    return exp.Literal.string(str(value))


def _format_scalar(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return repr(str(value))
