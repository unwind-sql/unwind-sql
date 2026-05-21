"""GET /api/column/{model}/{column} — recursive column-lineage tree."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from unwind.lineage import ColumnRef, get_column_lineage
from unwind.web.state import StateDep

router = APIRouter()


@router.get("/api/column/{model}/{column}")
def get_column(model: str, column: str, state: StateDep) -> dict[str, Any]:
    return _column_to_dict(get_column_lineage(state.project, model, column))


def _column_to_dict(node: ColumnRef) -> dict[str, Any]:
    return {
        "name": node.name,
        "expression": node.expression,
        "upstream": [_column_to_dict(c) for c in node.upstream],
    }
