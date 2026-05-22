"""FastAPI app factory: wires routes, exception handlers, and static assets."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from unwind.errors import UnwindError
from unwind.project import Project
from unwind.web.routes import cell, dag, data, impact, investigate, lineage, models
from unwind.web.state import build_state

if TYPE_CHECKING:
    # `pydantic_ai` is an optional extra — keep `Investigator` behind
    # TYPE_CHECKING so this module imports without it installed.
    from unwind.investigator import Investigator


_STATIC_DIR = Path(__file__).resolve().parent / "_static"


class _NoCacheHtmlStatic(StaticFiles):
    """StaticFiles subclass that serves `index.html` with `no-store`.

    The Vite bundle is content-hashed (`assets/index-<hash>.js`), so it can be
    cached aggressively — but `index.html` references the latest hashes and
    must not be stuck in the browser cache after a frontend redeploy.
    """

    async def get_response(self, path: str, scope: Scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        # Starlette normalises bare `/` to `.`; explicit /index.html stays as-is.
        if path in (".", "", "index.html") and response.status_code == 200:
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response


def build_app(
    project: Project,
    connection: duckdb.DuckDBPyConnection,
    *,
    investigator: Investigator | None = None,
) -> FastAPI:
    """Return a FastAPI app serving `project` from `connection`.

    Args:
        project: The project to expose. Must already be materialized on
            `connection` (i.e. the caller ran the project on it first).
        connection: The live DuckDB connection holding the run's data.
            The web app reads it directly — no re-materialization.
        investigator: Optional pre-built `Investigator`. If `None`, one is
            built lazily on the first `/api/investigate` call using the
            provider read from `UNWIND_LLM_PROVIDER` (default: `openai`).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.unwind = build_state(project, connection, investigator=investigator)
        yield

    app = FastAPI(title="unwind", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.exception_handler(UnwindError)
    async def _unwind_error_handler(_request: Request, exc: UnwindError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": str(exc)})

    app.include_router(dag.router)
    app.include_router(models.router)
    app.include_router(data.router)
    app.include_router(lineage.router)
    app.include_router(impact.router)
    app.include_router(cell.router)
    app.include_router(investigate.router)

    app.mount("/", _NoCacheHtmlStatic(directory=_STATIC_DIR, html=True), name="static")
    return app
