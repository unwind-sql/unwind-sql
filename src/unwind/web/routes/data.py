"""GET /api/model/{name}/data — paginated rows of a materialized model."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from unwind.errors import UnwindError
from unwind.runner import _quote_ident
from unwind.web._serialize import jsonable
from unwind.web.state import StateDep

router = APIRouter()

_MAX_LIMIT = 1000
_DEFAULT_LIMIT = 100


@router.get("/api/model/{name}/data")
def get_model_data(
    name: str,
    state: StateDep,
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    if name not in state.project.models:
        raise UnwindError(f"unknown model: {name!r}")
    qident = _quote_ident(name)
    cols_rows = state.conn.execute(f"DESCRIBE {qident}").fetchall()
    columns = [{"name": str(r[0]), "type": str(r[1])} for r in cols_rows]
    total_row = state.conn.execute(f"SELECT COUNT(*) FROM {qident}").fetchone()
    assert total_row is not None
    total = int(total_row[0])
    rows = state.conn.execute(
        f"SELECT * FROM {qident} LIMIT ? OFFSET ?", [limit, offset]
    ).fetchall()
    return {
        "columns": columns,
        "rows": [[jsonable(v) for v in row] for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
