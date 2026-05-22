"""Render a `Documentation` as a JSON-serialisable dict.

The output carries a `_schema` preamble describing every field — this is
specifically designed so an LLM consuming the manifest as a semantic layer
can interpret the structure without external instructions.
"""

from __future__ import annotations

from typing import Any

from unwind.docs.ir import Annotation, ColumnDoc, Documentation, ModelDoc

_SCHEMA = {
    "purpose": (
        "Semantic-layer manifest of an unwind project: every model, its "
        "columns, lineage edges, descriptions, and annotations. Designed to "
        "be passed verbatim as LLM context so the model can answer questions "
        "about the dataset (column meanings, upstream sources, business rules)."
    ),
    "fields": {
        "project_root": "Absolute path to the project root, or null when "
        "loaded from in-memory rows.",
        "models": "List of model objects in project order.",
        "model.name": "Unique identifier of the model.",
        "model.description": "Free-form human description (from leading `--` "
        "comments in SQL or the module docstring in Python).",
        "model.group": "Optional group label declared via `-- @group:` / `GROUP = …`.",
        "model.tags": "Free-form labels declared via `-- @tags:` / `TAGS = (...)`.",
        "model.materialized": "How the model is persisted: 'table', 'view', "
        "or 'external'.",
        "model.kind": "'sql' for a SQL model, 'python' for a Python model.",
        "model.upstreams": "Names of models this model directly depends on.",
        "model.downstreams": "Names of models that directly depend on this one.",
        "model.columns": "List of column objects. Empty for Python models when "
        "no live connection was passed to `Project.docs()`.",
        "model.annotations": "Free-form `--` comments inside the model body "
        "that were not attributed to a specific column, with line numbers.",
        "model.rendered_sql": "Final SQL after Jinja rendering; null for "
        "Python models.",
        "column.name": "Column identifier.",
        "column.type": "DuckDB type (e.g. 'VARCHAR', 'INTEGER'). Null when "
        "no connection was passed.",
        "column.description": "Human description. Either authored (trailing "
        "`--` on the SELECT projection) or inherited from an upstream column.",
        "column.inherited_from": "When the description was inherited, this "
        "is the source `model.column` reference. Null when the description "
        "is native or unavailable.",
        "column.stats": "Optional sample statistics. Present only when "
        "`with_stats=True` was passed to `Project.docs()`.",
    },
}


def render_json(doc: Documentation) -> dict[str, Any]:
    """Return a JSON-friendly dict for `doc`.

    Stable ordering: `models` is a list (not a dict) so consumers can rely on
    project insertion order. Everything is primitive (`dict`, `list`, `str`,
    `int`, `None`), so `json.dumps(result)` works out of the box.
    """
    return {
        "_schema": _SCHEMA,
        "project_root": str(doc.project_root) if doc.project_root else None,
        "models": [_render_model(m) for m in doc.models.values()],
    }


def render_model_json(model: ModelDoc) -> dict[str, Any]:
    """Render a single `ModelDoc` — used by the per-model API endpoint."""
    return _render_model(model)


def _render_model(model: ModelDoc) -> dict[str, Any]:
    return {
        "name": model.name,
        "description": model.description,
        "group": model.group,
        "tags": list(model.tags),
        "materialized": model.materialized,
        "kind": model.kind,
        "upstreams": list(model.upstreams),
        "downstreams": list(model.downstreams),
        "columns": [_render_column(c) for c in model.columns],
        "annotations": [_render_annotation(a) for a in model.annotations],
        "rendered_sql": model.rendered_sql,
    }


def _render_column(column: ColumnDoc) -> dict[str, Any]:
    return {
        "name": column.name,
        "type": column.type,
        "description": column.description,
        "inherited_from": column.inherited_from,
        "stats": (
            None
            if column.stats is None
            else {
                "row_count": column.stats.row_count,
                "null_count": column.stats.null_count,
                "distinct_count": column.stats.distinct_count,
            }
        ),
    }


def _render_annotation(annotation: Annotation) -> dict[str, Any]:
    return {"line": annotation.line, "text": annotation.text}
