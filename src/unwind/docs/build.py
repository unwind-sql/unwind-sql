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
  3. Propagate descriptions along column lineage: for every column without a
     native description, walk its upstream tree via `get_column_lineage` and
     adopt the first non-empty ancestor description.

The connection is optional so `project.docs()` works on a non-materialized
project (no types, no stats — just descriptions + lineage + structure).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

import duckdb

from unwind.dag import build_dag
from unwind.docs.ir import (
    Annotation,
    ColumnDoc,
    ColumnStats,
    Documentation,
    ModelDoc,
)
from unwind.docs.parser import parse_column_descriptions
from unwind.lineage import (
    LineageError,
    compute_qualified_sources,
    get_column_lineage,
)
from unwind.project import PythonModel
from unwind.runner import _quote_ident

if TYPE_CHECKING:
    from unwind.lineage import ColumnRef
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
    dag = build_dag(project)

    qualified_sources = _maybe_compute_qualified_sources(project, connection)

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
                    rendered_sql
                )
            kind = "sql"

        column_specs = _column_specs(
            model_name=name,
            native_descriptions=native_descriptions,
            connection=connection,
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
        downstreams = tuple(sorted(dag.downstream(name))) if node else ()

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

    if qualified_sources is not None:
        model_docs = _propagate_inherited_descriptions(
            project, model_docs, qualified_sources
        )

    return Documentation(project_root=project.root, models=model_docs)


def _maybe_compute_qualified_sources(
    project: Project, connection: duckdb.DuckDBPyConnection | None
) -> dict[str, str] | None:
    """Pre-compute `{model: qualified_sql}` when we have a materialized DB.

    Lineage inheritance needs sqlglot's lineage walker, which needs every
    `SELECT *` expanded — that's what `compute_qualified_sources` does. It
    requires a connection where every model is materialized. Without one,
    we skip inheritance (still cheap, still useful).
    """
    if connection is None:
        return None
    try:
        return compute_qualified_sources(project, connection=connection)
    except LineageError:
        return None


def _column_specs(
    *,
    model_name: str,
    native_descriptions: Mapping[str, str],
    connection: duckdb.DuckDBPyConnection | None,
) -> list[tuple[str, str | None, str | None]]:
    """Return `[(name, type, native_description), ...]` for a model.

    When `connection` is given, column order and types come from `DESCRIBE
    <model>`. Otherwise, we fall back to the column names found in the
    SELECT projection (no types). When neither yields anything, we return
    an empty list (e.g. Python model without a materialized DB).
    """
    if connection is not None:
        rows = connection.execute(f"DESCRIBE {_quote_ident(model_name)}").fetchall()
        return [
            (str(row[0]), str(row[1]), native_descriptions.get(str(row[0])))
            for row in rows
        ]
    return [
        (col, None, description)
        for col, description in native_descriptions.items()
    ]


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
    project: Project,
    model_docs: dict[str, ModelDoc],
    qualified_sources: dict[str, str],
) -> dict[str, ModelDoc]:
    """Fill in missing column descriptions from upstream lineage.

    For every `ColumnDoc` whose `description is None`, traverse the column
    lineage tree (sqlglot) and adopt the first non-empty description found
    on an ancestor — recording its source in `inherited_from`. Native
    descriptions are never overwritten.

    Python models have no SQL AST so `get_column_lineage` would raise; we
    skip them.
    """
    native_index = _build_native_description_index(model_docs)

    updated: dict[str, ModelDoc] = {}
    for name, doc in model_docs.items():
        if doc.kind != "sql" or not doc.columns:
            updated[name] = doc
            continue

        new_columns: list[ColumnDoc] = []
        for column in doc.columns:
            if column.description is not None:
                new_columns.append(column)
                continue
            inherited = _find_inherited(
                project, name, column.name, qualified_sources, native_index
            )
            if inherited is None:
                new_columns.append(column)
            else:
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


def _build_native_description_index(
    model_docs: dict[str, ModelDoc],
) -> dict[tuple[str, str], str]:
    """Map `(model, column)` → native description for fast lookup during inheritance."""
    index: dict[tuple[str, str], str] = {}
    for model_name, doc in model_docs.items():
        for column in doc.columns:
            if column.description is not None and column.inherited_from is None:
                index[(model_name, column.name.lower())] = column.description
    return index


def _find_inherited(
    project: Project,
    target_model: str,
    column_name: str,
    qualified_sources: dict[str, str],
    native_index: dict[tuple[str, str], str],
) -> tuple[str, str] | None:
    """Return `(source_ref, description)` for the first documented ancestor."""
    try:
        root = get_column_lineage(
            project,
            target_model,
            column_name,
            qualified_sources=qualified_sources,
        )
    except LineageError:
        return None

    return _walk_for_description(root, skip_root=True, native_index=native_index)


def _walk_for_description(
    node: ColumnRef,
    *,
    skip_root: bool,
    native_index: dict[tuple[str, str], str],
) -> tuple[str, str] | None:
    """DFS through `node` upstream. `name` is `MODEL.COLUMN` per sqlglot."""
    if not skip_root:
        ref = _split_ref(node.name)
        if ref is not None:
            description = native_index.get((ref[0], ref[1].lower()))
            if description is not None:
                return f"{ref[0]}.{ref[1]}", description
    for child in node.upstream:
        found = _walk_for_description(
            child, skip_root=False, native_index=native_index
        )
        if found is not None:
            return found
    return None


def _split_ref(qualified: str) -> tuple[str, str] | None:
    """Split sqlglot's `MODEL.COLUMN` reference; return `None` if unparseable."""
    if "." not in qualified:
        return None
    model, _, column = qualified.rpartition(".")
    if not model or not column:
        return None
    return model, column
