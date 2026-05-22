"""GET /api/docs — semantic-layer documentation for the project.

Three endpoints:

  - ``GET /api/docs``                  → full `Documentation.to_json()`
  - ``GET /api/docs/{name}``           → one model's JSON entry
  - ``GET /api/docs/export?format=…``  → downloadable Markdown or JSON file

The `Documentation` object is built once on first request and cached on
the app state — it is pure derivation of project + connection state, both
of which are stable for the life of the FastAPI app (see `web/state.py`).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from unwind.docs.ir import Documentation
from unwind.errors import UnwindError
from unwind.web.state import AppState, StateDep

router = APIRouter()


@router.get("/api/docs")
def get_docs(state: StateDep) -> dict[str, Any]:
    """Return the full documentation as JSON."""
    return _documentation(state).to_json()


@router.get("/api/docs/export")
def export_docs(
    state: StateDep,
    fmt: str = Query("markdown", alias="format"),
) -> Response:
    """Return the documentation as a downloadable file (`markdown` or `json`).

    Declared **before** `/api/docs/{name}` so FastAPI matches the literal
    `"export"` segment instead of treating it as a model name.
    """
    doc = _documentation(state)
    if fmt == "markdown":
        body = doc.to_markdown()
        return Response(
            content=body,
            media_type="text/markdown",
            headers={"Content-Disposition": 'attachment; filename="unwind-docs.md"'},
        )
    if fmt == "json":
        body = json.dumps(doc.to_json(), ensure_ascii=False, indent=2)
        return Response(
            content=body,
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="unwind-docs.json"',
            },
        )
    raise HTTPException(
        status_code=400,
        detail=f"unsupported format {fmt!r}; expected 'markdown' or 'json'",
    )


@router.get("/api/docs/{name}")
def get_doc(name: str, state: StateDep) -> dict[str, Any]:
    """Return the documentation entry for one model.

    Renders only the requested model — on a 100+ model project the whole-
    project JSON would otherwise be megabytes of `rendered_sql` repeated
    per request.
    """
    doc = _documentation(state)
    if name not in doc.models:
        raise UnwindError(f"unknown model: {name!r}")
    from unwind.docs.render_json import render_model_json  # noqa: PLC0415

    return render_model_json(doc.models[name])


def _documentation(state: AppState) -> Documentation:
    """Lazily build and cache the `Documentation` on app state.

    `build_documentation` does at most one `compute_qualified_sources` pass
    plus a few `DESCRIBE` queries — fast, but no point repeating per request.
    The result is immutable for the life of the run.
    """
    if state.documentation is not None:
        return state.documentation
    from unwind.docs.build import build_documentation  # noqa: PLC0415

    state.documentation = build_documentation(
        state.project, connection=state.conn, with_stats=False
    )
    return state.documentation
