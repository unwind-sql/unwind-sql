"""GET /api/dag — nodes, edges, and group memberships of the project DAG."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from unwind.project import PythonModel
from unwind.runner import _quote_ident
from unwind.web.state import AppState, StateDep

router = APIRouter()


@router.get("/api/dag")
def get_dag(state: StateDep) -> dict[str, Any]:
    return _dag_payload(state)


def _dag_payload(state: AppState) -> dict[str, Any]:
    project = state.project
    dag = state.dag

    nodes: list[dict[str, Any]] = []
    group_members: dict[str, list[str]] = {}
    for name in dag.nodes:
        model = project.models[name]
        # External models override the prefix-based kind so they stand out as
        # output sinks in the DAG view.
        kind = "external" if model.materialized == "external" else _classify(name)
        nodes.append(
            {
                "id": name,
                "kind": kind,
                "language": "python" if isinstance(model, PythonModel) else "sql",
                "group": model.group,
                "tags": list(model.tags),
                "row_count": _count_rows(state, name),
                "materialized": model.materialized,
                "location": model.rendered_location,
            }
        )
        if model.group is not None:
            group_members.setdefault(model.group, []).append(name)

    edges = [
        {"from": parent, "to": node.name}
        for node in dag.nodes.values()
        for parent in node.depends_on_models
    ]
    groups = [{"id": gid, "members": members} for gid, members in group_members.items()]
    return {"nodes": nodes, "edges": edges, "groups": groups}


def _classify(name: str) -> str:
    for prefix in ("raw", "ref", "stg", "int", "fct", "dim", "mart"):
        if name.startswith(f"{prefix}_") or name == prefix:
            return prefix
    return "model"


def _count_rows(state: AppState, name: str) -> int | None:
    """Best-effort row count for the materialized model. None on failure."""
    try:
        row = state.conn.execute(f"SELECT COUNT(*) FROM {_quote_ident(name)}").fetchone()
    except Exception:
        return None
    return None if row is None else int(row[0])
