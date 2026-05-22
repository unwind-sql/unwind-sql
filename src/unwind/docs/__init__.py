"""Automatic documentation generation for unwind projects.

Public surface (also re-exported from `unwind`):

    - `Documentation`, `ModelDoc`, `ColumnDoc`, `Annotation`, `ColumnStats`
    - `build_documentation(project, *, connection=None, with_stats=False)`

`Documentation.to_markdown()` / `.to_json()` render the IR for humans
(Markdown/PDF) and for LLMs (semantic-layer manifest).
"""

from __future__ import annotations

from unwind.docs.build import build_documentation
from unwind.docs.ir import (
    Annotation,
    ColumnDoc,
    ColumnStats,
    Documentation,
    ModelDoc,
)

__all__ = [
    "Annotation",
    "ColumnDoc",
    "ColumnStats",
    "Documentation",
    "ModelDoc",
    "build_documentation",
]
