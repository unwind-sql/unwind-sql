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
from unwind.project import Project, PythonModel
from unwind.runner import _materialize_disabled, materialize_model

if TYPE_CHECKING:
    # `pydantic_ai` is an optional extra, so types from `unwind.investigator`
    # stay behind TYPE_CHECKING to keep this module importable without it.
    from unwind.investigator import Explanation, Investigator


CacheKey = tuple[str, str, frozenset[tuple[str, Any]]]


@dataclass(slots=True)
class AppState:
    """Container for everything the web routes need at runtime."""

    project: Project
    dag: DAG
    conn: duckdb.DuckDBPyConnection
    # Memoized {model: qualified_sql} — expensive to compute (one DESCRIBE per
    # model + sqlglot's `qualify` pass), so cache and reuse across requests.
    # Built on first access via `app_state.qualified_sources()`.
    _qualified_sources: dict[str, str] | None = None
    investigator: Investigator | None = None  # built lazily on first /api/investigate
    explanation_cache: OrderedDict[CacheKey, Explanation] = field(default_factory=OrderedDict)

    def close(self) -> None:
        self.conn.close()

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


def build_state(project: Project, *, investigator: Investigator | None = None) -> AppState:
    """Render the project (if needed), then materialize every model as a VIEW.

    VIEW (not TABLE) materialization is deliberate: it makes bootstrap near
    instant on large DAGs by deferring the actual scan to whenever a route
    queries the data. Trace and lineage routes only ever read a handful of
    rows at a time, so the lazy cost is bounded.
    """
    rendered = project if _is_rendered(project) else project.render()
    dag = rendered.dag()
    conn = duckdb.connect(":memory:")
    for name in dag.execution_order:
        model = rendered.models[name]
        if model.disabled:
            parents = sorted(dag.nodes[name].depends_on_models)
            _materialize_disabled(conn, name, parents, debug=False)
            continue
        materialize_model(
            conn,
            model,
            variables={},
            project_root=rendered.root,
            # The web UI doesn't write parquets — coerce external models into
            # plain tables so the data is still queryable in-process.
            respect_external=False,
            # Force every model to be a VIEW so bootstrap stays cheap.
            view_only=True,
        )
    return AppState(project=rendered, dag=dag, conn=conn, investigator=investigator)


def _is_rendered(project: Project) -> bool:
    return all(
        isinstance(m, PythonModel) or m.rendered_sql is not None
        for m in project.models.values()
    )


def _get_state(request: Request) -> AppState:
    return request.app.state.unwind  # set by build_app's lifespan


StateDep = Annotated[AppState, Depends(_get_state)]
