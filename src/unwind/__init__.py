"""Unwind: SQL orchestration with templating, semantic layer, and lineage."""

from typing import TYPE_CHECKING, Any

from unwind.__about__ import __version__
from unwind.dag import DAG, DAGError, Node
from unwind.db_loader import load_from_db
from unwind.errors import ProjectLoadError, TemplateRenderError, UnwindError
from unwind.impact import ColumnImpact, ImpactedColumn, ImpactEdge, ImpactError
from unwind.lineage import ColumnRef, LineageError, TableLineage
from unwind.loader import load
from unwind.project import Model, ModelContext, Project, PythonModel
from unwind.runner import ExecutedModel, RunError, RunResult
from unwind.trace import TraceError, TraceNode, TraceResult

if TYPE_CHECKING:
    from unwind.investigator import (
        Explanation,
        Finding,
        Investigator,
        InvestigatorError,
    )

__all__ = [
    "DAG",
    "ColumnImpact",
    "ColumnRef",
    "DAGError",
    "ExecutedModel",
    "Explanation",
    "Finding",
    "ImpactEdge",
    "ImpactError",
    "ImpactedColumn",
    "Investigator",
    "InvestigatorError",
    "LineageError",
    "Model",
    "ModelContext",
    "Node",
    "Project",
    "ProjectLoadError",
    "PythonModel",
    "RunError",
    "RunResult",
    "TableLineage",
    "TemplateRenderError",
    "TraceError",
    "TraceNode",
    "TraceResult",
    "UnwindError",
    "__version__",
    "load",
    "load_from_db",
]


_LLM_NAMES = frozenset({"Investigator", "Explanation", "Finding", "InvestigatorError"})


def __getattr__(name: str) -> Any:
    """Lazy-load `Investigator` and friends so `pydantic-ai` stays optional."""
    if name in _LLM_NAMES:
        try:
            from unwind import investigator  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                f"unwind.{name} requires pydantic-ai; install with `uv pip install unwind[llm]`"
            ) from exc
        return getattr(investigator, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
