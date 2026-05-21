"""FastAPI-based web UI for browsing a Project's DAG and column lineage.

`Project.show()` calls `serve()` which runs the pipeline once on an in-memory
DuckDB connection, then serves a Vite/React SPA from `_static/` plus a small
JSON API:

- ``GET /api/dag`` — nodes (with `kind` for raw/ref/int/fct) and edges
- ``GET /api/model/<name>`` — sql, columns (DuckDB DESCRIBE), row count,
  upstream/downstream model names
- ``GET /api/column/<model>/<column>`` — recursive column-lineage tree

The frontend lives under `web-client/` (Vite + React + TypeScript +
@xyflow/react + @dagrejs/dagre). `bun run build` writes the production
bundle directly into this package's `_static/` directory; the bundle is
versioned in git so installing the wheel does not require Node. See
`web-client/README.md` for the dev workflow.

Requires the optional `[web]` extra (FastAPI + Uvicorn).
"""

from unwind.web.app import build_app
from unwind.web.errors import WebServerError
from unwind.web.server import serve

__all__ = ["WebServerError", "build_app", "serve"]
