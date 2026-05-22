"""GET /api/column/{model}/{column}/impact — downstream impact analysis.

Walks the DAG forward from `(model, column)` and reports every affected
downstream column plus the edges that explain why. Opaque Python consumers
are surfaced separately so the UI can flag them without trying to expand
their dependencies.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from unwind.impact import ColumnImpact, get_column_impact
from unwind.web.state import StateDep

router = APIRouter()


@router.get("/api/column/{model}/{column}/impact")
def get_impact(model: str, column: str, state: StateDep) -> dict[str, Any]:
    return _to_dict(get_column_impact(state.project, model, column, connection=state.conn))


def _to_dict(impact: ColumnImpact) -> dict[str, Any]:
    return {
        "source": {
            "model": impact.source_model,
            "column": impact.source_column,
            "type": impact.source_type,
        },
        "affected": [
            {
                "model": c.model,
                "column": c.column,
                "type": c.column_type,
                "expression": c.expression,
            }
            for c in impact.affected
        ],
        "edges": [
            {
                "parent_model": e.parent_model,
                "parent_column": e.parent_column,
                "child_model": e.child_model,
                "child_column": e.child_column,
                "usage": e.usage,
            }
            for e in impact.edges
        ],
        "opaque_consumers": list(impact.opaque_consumers),
    }
