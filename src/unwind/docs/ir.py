"""Documentation IR: frozen dataclasses describing a project's models.

`Documentation` is the root container returned by `Project.docs()`. It is
intentionally pure data — no methods that touch DuckDB or sqlglot — so it can
be cached, serialised, diffed, and passed around freely. Rendering is layered
on top: `to_markdown()` lives in `docs.render_md`, `to_json()` in
`docs.render_json`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ColumnStats:
    """Sample statistics computed once when `with_stats=True` is requested."""

    row_count: int
    null_count: int
    distinct_count: int


@dataclass(frozen=True, slots=True)
class ColumnDoc:
    """A single column of a model.

    `description` is either authored by the user (trailing `--` on the SELECT
    projection) or inherited from an upstream column with the same lineage.
    `inherited_from` is `None` for native descriptions and set to
    `"<model>.<column>"` for inherited ones.
    """

    name: str
    type: str | None
    description: str | None
    inherited_from: str | None
    stats: ColumnStats | None


@dataclass(frozen=True, slots=True)
class Annotation:
    """A free-form comment in the model body that is not attributed to a column."""

    line: int  # 1-indexed inside `rendered_sql`
    text: str


@dataclass(frozen=True, slots=True)
class ModelDoc:
    """Documentation for one model in the project."""

    name: str
    description: str | None
    group: str | None
    tags: tuple[str, ...]
    materialized: str
    kind: str  # "sql" | "python"
    columns: tuple[ColumnDoc, ...]
    annotations: tuple[Annotation, ...]
    upstreams: tuple[str, ...]
    downstreams: tuple[str, ...]
    rendered_sql: str | None  # `None` for Python models


@dataclass(frozen=True, slots=True)
class Documentation:
    """Top-level documentation object: one `ModelDoc` per model in the project."""

    project_root: Path | None
    models: dict[str, ModelDoc]

    def to_markdown(self) -> str:
        """Render the documentation as a single Markdown document."""
        from unwind.docs.render_md import render_markdown  # noqa: PLC0415

        return render_markdown(self)

    def to_json(self) -> dict[str, Any]:
        """Serialise the documentation as a JSON-friendly dict (humans + LLM)."""
        from unwind.docs.render_json import render_json  # noqa: PLC0415

        return render_json(self)
