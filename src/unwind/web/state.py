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
from unwind.runner import materialize_model

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
    investigator: Investigator | None = None  # built lazily on first /api/investigate
    explanation_cache: OrderedDict[CacheKey, Explanation] = field(default_factory=OrderedDict)

    def close(self) -> None:
        self.conn.close()


def build_state(project: Project, *, investigator: Investigator | None = None) -> AppState:
    """Render the project (if needed), materialize every model on DuckDB."""
    rendered = project if _is_rendered(project) else project.render()
    dag = rendered.dag()
    conn = duckdb.connect(":memory:")
    for name in dag.execution_order:
        materialize_model(
            conn,
            rendered.models[name],
            variables={},
            project_root=rendered.root,
            # The web UI doesn't write parquets — coerce external models into
            # plain tables so the data is still queryable in-process.
            respect_external=False,
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
