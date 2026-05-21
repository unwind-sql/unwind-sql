"""Public exception hierarchy."""

from __future__ import annotations


class UnwindError(Exception):
    """Base class for all unwind-raised errors."""


class ProjectLoadError(UnwindError):
    """Raised when a project directory cannot be loaded."""


class TemplateRenderError(UnwindError):
    """Raised when Jinja rendering of a model fails."""

    def __init__(self, model_name: str, message: str) -> None:
        super().__init__(f"failed to render model {model_name!r}: {message}")
        self.model_name = model_name
