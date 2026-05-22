"""POST /api/cell — value lineage for a single cell.

The frontend builds the `where` exhaustively from every scalar column of the
clicked row, so the predicate uniquely identifies one row in the target model.
The response is the serialized `TraceResult` tree (formula + substituted form
+ values + predicate per node).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from unwind.trace import TraceNode, trace_value
from unwind.web._serialize import jsonable
from unwind.web.state import StateDep

router = APIRouter()


class CellRequest(BaseModel):
    model: str
    column: str
    where: dict[str, Any] = Field(default_factory=dict)
    depth: int | None = None
    max_values: int | None = 5


@router.post("/api/cell")
def get_cell(req: CellRequest, state: StateDep) -> dict[str, Any]:
    result = trace_value(
        state.project,
        model=req.model,
        column=req.column,
        where=req.where,
        depth=req.depth,
        max_values=req.max_values,
        connection=state.conn,
        qualified_sources=state.qualified_sources(),
    )
    return {
        "model": result.model,
        "column": result.column,
        "where": _coerce_dict(result.where),
        "root": _node_to_dict(result.root),
    }


def _node_to_dict(node: TraceNode) -> dict[str, Any]:
    return {
        "model": node.model,
        "column": node.column,
        "expression": node.expression,
        "substituted": node.substituted,
        "values": [jsonable(v) for v in node.values],
        "value_count": node.value_count,
        "predicate": _coerce_dict(node.predicate),
        "upstream": [_node_to_dict(c) for c in node.upstream],
    }


def _coerce_dict(d: dict[str, Any]) -> dict[str, Any]:
    return {k: jsonable(v) for k, v in d.items()}
