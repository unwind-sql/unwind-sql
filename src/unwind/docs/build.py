"""Assemble a `Documentation` from a `Project` (+ optional DuckDB connection).

Pipeline:

  1. Walk the rendered project. For each SQL model, extract native column
     descriptions (trailing `--` on outermost SELECT) and free annotations
     via `parser.parse_column_descriptions`. The model description was
     already captured by the loader.
  2. If a `DuckDBPyConnection` is provided (typically the live connection
     from `Project.run()`), enrich every column with its DuckDB type via
     `DESCRIBE` and — when `with_stats=True` — a single aggregated stats
     query per model (`COUNT(*)`, `COUNT(col)`, `COUNT(DISTINCT col)`).
  3. Propagate descriptions along the DAG: in topological order, every
     column without a native description tries to inherit it from a
     same-named column in any direct upstream model (recording the source
     in `inherited_from`). This is the cheap pass — no full sqlglot
     lineage walk, just name-based lookups against an index. Users who
     need precise lineage call `Project.get_column_lineage(...)` directly.

The connection is optional so `project.docs()` works on a non-materialized
project (no types, no stats — just descriptions + structure).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from unwind._sql import DIALECT
from unwind.dag import DAG, build_dag
from unwind.docs.ir import (
    Annotation,
    ColumnDoc,
    ColumnStats,
    Documentation,
    ModelDoc,
)
from unwind.docs.parser import parse_column_descriptions
from unwind.project import PythonModel
from unwind.runner import _quote_ident

if TYPE_CHECKING:
    from unwind.project import Project


def build_documentation(
    project: Project,
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
    with_stats: bool = False,
) -> Documentation:
    """Return a `Documentation` for `project`.

    Args:
        project: A rendered `Project`. Callers usually pass
            `project.render(...)` or rely on `Project.docs()` which renders
            lazily.
        connection: If given, enrich columns with DuckDB types and, when
            `with_stats=True`, sample stats. Must point to a connection
            where every non-disabled model is already materialized
            (typically the one from `Project.run()`).
        with_stats: When `True` and `connection` is set, emit one
            aggregated stats query per model.

    Returns:
        A `Documentation` object whose `models` mapping preserves the
        project's model order.
    """
    parsed_trees = _parse_model_trees(project)
    dag = build_dag(project, parsed_trees=parsed_trees)
    direct_children = _direct_children(dag)
    # Pre-fetch all column types in one round-trip — issuing 100+ DESCRIBE
    # statements over a freshly-opened on-disk DuckDB file is the biggest
    # single cost on wide projects.
    schema_by_model = (
        _fetch_all_columns(connection, project) if connection is not None else {}
    )

    model_docs: dict[str, ModelDoc] = {}
    for name, model in project.models.items():
        if isinstance(model, PythonModel):
            native_descriptions: dict[str, str] = {}
            annotations: tuple[Annotation, ...] = ()
            rendered_sql: str | None = None
            kind = "python"
        else:
            rendered_sql = model.rendered_sql
            if rendered_sql is None:
                native_descriptions, annotations = {}, ()
            else:
                native_descriptions, annotations = parse_column_descriptions(
                    rendered_sql, parsed_tree=parsed_trees.get(name)
                )
            kind = "sql"

        column_specs = _column_specs(
            model_name=name,
            native_descriptions=native_descriptions,
            connection=connection,
            schema=schema_by_model.get(name),
        )

        stats_by_column = (
            _fetch_stats(connection, name, [c[0] for c in column_specs])
            if with_stats and connection is not None and column_specs
            else {}
        )

        columns = tuple(
            ColumnDoc(
                name=col_name,
                type=col_type,
                description=description,
                inherited_from=None,
                stats=stats_by_column.get(col_name),
            )
            for col_name, col_type, description in column_specs
        )

        node = dag.nodes.get(name)
        upstreams = tuple(sorted(node.depends_on_models)) if node else ()
        downstreams = tuple(sorted(direct_children.get(name, ()))) if node else ()

        model_docs[name] = ModelDoc(
            name=name,
            description=model.description,
            group=model.group,
            tags=model.tags,
            materialized=model.materialized,
            kind=kind,
            columns=columns,
            annotations=annotations,
            upstreams=upstreams,
            downstreams=downstreams,
            rendered_sql=rendered_sql,
        )

    model_docs = _propagate_inherited_descriptions(dag, model_docs)

    return Documentation(project_root=project.root, models=model_docs)


def _parse_model_trees(project: Project) -> dict[str, exp.Expression]:
    """Parse every SQL model's rendered SQL once, shared by `build_dag` and the
    column-description parser. Failures are skipped — those callers fall
    back to re-parsing (and surface the proper error there) so the docs
    pipeline doesn't crash on a single bad model.
    """
    trees: dict[str, exp.Expression] = {}
    for name, model in project.models.items():
        if isinstance(model, PythonModel):
            continue
        if model.rendered_sql is None:
            continue
        try:
            parsed = sqlglot.parse_one(model.rendered_sql, dialect=DIALECT)
        except SqlglotError:
            continue
        trees[name] = cast("exp.Expression", parsed)
    return trees


def _direct_children(dag: DAG) -> dict[str, set[str]]:
    """Invert the DAG's parent edges in O(N) to look up direct children fast."""
    children: dict[str, set[str]] = {n: set() for n in dag.nodes}
    for node in dag.nodes.values():
        for parent in node.depends_on_models:
            children.setdefault(parent, set()).add(node.name)
    return children


def _column_specs(
    *,
    model_name: str,
    native_descriptions: Mapping[str, str],
    connection: duckdb.DuckDBPyConnection | None,
    schema: list[tuple[str, str]] | None,
) -> list[tuple[str, str | None, str | None]]:
    """Return `[(name, type, native_description), ...]` for a model.

    When a `schema` (pre-fetched from `information_schema.columns`) is
    available, column order and types come from there — no per-model
    DuckDB round-trip. When `schema` is missing but `connection` is set,
    we fall back to `DESCRIBE` so the build still works on models that
    weren't materialised (e.g. disabled). Without either, we use the names
    found in the SELECT projection (no types).
    """
    if schema is not None:
        return [
            (col_name, col_type, native_descriptions.get(col_name))
            for col_name, col_type in schema
        ]
    if connection is not None:
        try:
            rows = connection.execute(
                f"DESCRIBE {_quote_ident(model_name)}"
            ).fetchall()
        except duckdb.Error:
            return []
        return [
            (str(row[0]), str(row[1]), native_descriptions.get(str(row[0])))
            for row in rows
        ]
    return [
        (col, None, description)
        for col, description in native_descriptions.items()
    ]


def _fetch_all_columns(
    connection: duckdb.DuckDBPyConnection,
    project: Project,
) -> dict[str, list[tuple[str, str]]]:
    """Return `{model_name: [(column, type), …]}` in one round-trip.

    DuckDB's `information_schema.columns` exposes every column of every
    table in the current database, with a stable `ordinal_position`. One
    query replaces N `DESCRIBE` statements — on a 100+ model project, this
    saves several seconds.

    Models that aren't materialised (Python sinks, disabled models) simply
    won't appear; callers fall back to `DESCRIBE` per model in that case.
    """
    model_names = list(project.models)
    if not model_names:
        return {}
    placeholders = ", ".join(["?"] * len(model_names))
    sql = (
        "SELECT table_name, column_name, data_type "
        "FROM information_schema.columns "
        f"WHERE table_name IN ({placeholders}) "
        "ORDER BY table_name, ordinal_position"
    )
    try:
        rows = connection.execute(sql, model_names).fetchall()
    except duckdb.Error:
        return {}
    result: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        table = str(row[0])
        result.setdefault(table, []).append((str(row[1]), str(row[2])))
    return result


def _fetch_stats(
    connection: duckdb.DuckDBPyConnection,
    model_name: str,
    columns: list[str],
) -> dict[str, ColumnStats]:
    """Run one aggregated query per model and return `{column: ColumnStats}`.

    Shape: `SELECT COUNT(*), COUNT(c1), COUNT(DISTINCT c1), COUNT(c2), ...`
    The single round-trip keeps `with_stats=True` cheap even on wide tables.
    """
    if not columns:
        return {}
    quoted = _quote_ident(model_name)
    parts = ["COUNT(*)"]
    for col in columns:
        qcol = _quote_ident(col)
        parts.append(f"COUNT({qcol})")
        parts.append(f"COUNT(DISTINCT {qcol})")
    sql = f"SELECT {', '.join(parts)} FROM {quoted}"
    row = connection.execute(sql).fetchone()
    if row is None:
        return {}
    total = int(row[0])
    out: dict[str, ColumnStats] = {}
    for i, col in enumerate(columns):
        non_null = int(row[1 + 2 * i])
        distinct = int(row[2 + 2 * i])
        out[col] = ColumnStats(
            row_count=total,
            null_count=total - non_null,
            distinct_count=distinct,
        )
    return out


def _propagate_inherited_descriptions(
    dag: DAG,
    model_docs: dict[str, ModelDoc],
) -> dict[str, ModelDoc]:
    """Fill missing column descriptions from same-named upstream columns.

    Walks the project in topological order. For each column without a native
    description, looks up `column_name` in every direct upstream model: the
    first hit (native description, or one already inherited earlier in the
    pass) wins. Transitive inheritance happens for free thanks to the topo
    walk — by the time we visit M, every model upstream of M has its
    `description_index` entry filled.

    This is *intentionally* a cheap heuristic. It correctly handles the
    common patterns (`SELECT *`, simple renames, alias pass-throughs) and
    quietly skips opaque cases (joins with same-named columns from multiple
    sides, mid-pipeline computations). Users who need authoritative lineage
    can still call `Project.get_column_lineage(...)`.
    """
    description_index: dict[tuple[str, str], tuple[str | None, str]] = {}
    for model_name, doc in model_docs.items():
        for column in doc.columns:
            if column.description is not None and column.inherited_from is None:
                description_index[(model_name, column.name.lower())] = (
                    None,
                    column.description,
                )

    updated: dict[str, ModelDoc] = dict(model_docs)
    for name in dag.execution_order:
        doc = updated.get(name)
        if doc is None or doc.kind != "sql" or not doc.columns:
            continue
        node = dag.nodes.get(name)
        if node is None or not node.depends_on_models:
            continue
        upstream_models = [m for m in node.depends_on_models if m in updated]
        if not upstream_models:
            continue

        new_columns: list[ColumnDoc] = []
        changed = False
        for column in doc.columns:
            if column.description is not None:
                new_columns.append(column)
                continue
            inherited = _lookup_upstream_description(
                column.name, upstream_models, description_index
            )
            if inherited is None:
                new_columns.append(column)
                continue
            source, text = inherited
            new_columns.append(
                ColumnDoc(
                    name=column.name,
                    type=column.type,
                    description=text,
                    inherited_from=source,
                    stats=column.stats,
                )
            )
            description_index[(name, column.name.lower())] = (source, text)
            changed = True

        if changed:
            updated[name] = ModelDoc(
                name=doc.name,
                description=doc.description,
                group=doc.group,
                tags=doc.tags,
                materialized=doc.materialized,
                kind=doc.kind,
                columns=tuple(new_columns),
                annotations=doc.annotations,
                upstreams=doc.upstreams,
                downstreams=doc.downstreams,
                rendered_sql=doc.rendered_sql,
            )
    return updated


def _lookup_upstream_description(
    column_name: str,
    upstream_models: list[str],
    description_index: dict[tuple[str, str], tuple[str | None, str]],
) -> tuple[str, str] | None:
    """Return `(source_ref, description)` from the first upstream that has a match."""
    key = column_name.lower()
    for upstream in upstream_models:
        entry = description_index.get((upstream, key))
        if entry is not None:
            return f"{upstream}.{column_name}", entry[1]
    return None
