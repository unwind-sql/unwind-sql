"""GET /api/model/{name} — sql, columns, row count, neighbours."""

from __future__ import annotations

from typing import Any

import duckdb
from fastapi import APIRouter

from unwind.errors import UnwindError
from unwind.project import ModelOrPython, PythonModel
from unwind.runner import _quote_ident
from unwind.web.state import StateDep

router = APIRouter()


@router.get("/api/model/{name}")
def get_model(name: str, state: StateDep) -> dict[str, Any]:
    if name not in state.project.models:
        raise UnwindError(f"unknown model: {name!r}")
    model = state.project.models[name]
    columns = _describe_columns(state.conn, name)
    row = state.conn.execute(f"SELECT COUNT(*) FROM {_quote_ident(name)}").fetchone()
    assert row is not None
    source, language = _source_and_language(model)
    return {
        "name": name,
        "language": language,
        "source": source,
        "row_count": int(row[0]),
        "columns": columns,
        "upstream": sorted(state.dag.nodes[name].depends_on_models),
        "downstream": sorted(state.dag.downstream(name)),
    }


def _source_and_language(model: ModelOrPython) -> tuple[str, str]:
    """Return `(source_text, language)` for a SQL or Python model.

    For Python models we read the on-disk file so the UI can show the actual
    `def model(context): ...` body. The reads are bounded by the project size
    (one model per file) and happen at most once per panel click.
    """
    if isinstance(model, PythonModel):
        path = model.path
        if path is not None:
            try:
                return path.read_text(encoding="utf-8"), "python"
            except OSError as exc:
                return f"# could not read {path}: {exc}", "python"
        return f"# Python model loaded from {model.origin}\n# (no source path)", "python"
    return model.rendered_sql or "", "sql"


def _describe_columns(conn: duckdb.DuckDBPyConnection, model_name: str) -> list[dict[str, str]]:
    rows = conn.execute(f"DESCRIBE {_quote_ident(model_name)}").fetchall()
    return [{"name": str(r[0]), "type": str(r[1])} for r in rows]
