"""AppState: holds the rendered project, DAG, and DuckDB connection.

Built once at app startup (FastAPI lifespan) and exposed to routes via
`StateDep`. The DuckDB connection is single-threaded by design — uvicorn is
configured single-worker and the UI is single-user.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any

import duckdb
from fastapi import Depends, Request

from unwind.dag import DAG
from unwind.project import Project

if TYPE_CHECKING:
    # `pydantic_ai` is an optional extra, so types from `unwind.investigator`
    # stay behind TYPE_CHECKING to keep this module importable without it.
    from unwind.docs.ir import Documentation
    from unwind.investigator import Explanation, Investigator


CacheKey = tuple[str, str, frozenset[tuple[str, Any]]]


@dataclass(slots=True)
class AppState:
    """Container for everything the web routes need at runtime."""

    project: Project
    dag: DAG
    conn: duckdb.DuckDBPyConnection
    # Row counts captured by the runner during materialization. Used by
    # /api/dag so we don't re-issue 100+ `SELECT COUNT(*)` queries — most
    # of which would hit views and re-execute their full SQL chain.
    row_counts: dict[str, int] = field(default_factory=dict)
    # Memoized {model: qualified_sql} — expensive to compute (one DESCRIBE per
    # model + sqlglot's `qualify` pass), so cache and reuse across requests.
    # Built on first access via `app_state.qualified_sources()`.
    _qualified_sources: dict[str, str] | None = None
    investigator: Investigator | None = None  # built lazily on first /api/investigate
    explanation_cache: OrderedDict[CacheKey, Explanation] = field(default_factory=OrderedDict)
    # Built lazily on the first /api/docs* call; immutable for the life of the run.
    documentation: Documentation | None = None

    def qualified_sources(self) -> dict[str, str]:
        """Lazily compute and memoize per-model qualified SQL.

        Used by lineage / impact routes — the result depends only on the
        project schema, not on per-request inputs, so one call per process is
        enough.
        """
        if self._qualified_sources is None:
            from unwind.lineage import compute_qualified_sources  # noqa: PLC0415

            self._qualified_sources = compute_qualified_sources(
                self.project, connection=self.conn
            )
        return self._qualified_sources


def build_state(
    project: Project,
    connection: duckdb.DuckDBPyConnection,
    *,
    row_counts: dict[str, int] | None = None,
    investigator: Investigator | None = None,
) -> AppState:
    """Wrap a project + its already-materialized DuckDB connection.

    The caller is expected to have run the project on `connection` first
    (typically via `RunResult` from `Project.run()`). The web UI reads
    straight from that connection — no re-execution, no recompute.

    `row_counts`, if given, seeds the per-model row-count cache used by
    `/api/dag`; otherwise the route falls back to live `SELECT COUNT(*)`
    queries (slow when many models are views).
    """
    rendered = project if _is_rendered(project) else project.render()
    return AppState(
        project=rendered,
        dag=rendered.dag(),
        conn=connection,
        row_counts=dict(row_counts) if row_counts else {},
        investigator=investigator,
    )


def _is_rendered(project: Project) -> bool:
    from unwind.project import PythonModel  # noqa: PLC0415

    return all(
        isinstance(m, PythonModel) or m.rendered_sql is not None
        for m in project.models.values()
    )


def _get_state(request: Request) -> AppState:
    return request.app.state.unwind  # set by build_app's lifespan


StateDep = Annotated[AppState, Depends(_get_state)]
