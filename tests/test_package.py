"""Smoke tests for the package skeleton."""

from __future__ import annotations

import unwind


def test_version_is_exposed() -> None:
    assert isinstance(unwind.__version__, str)
    assert unwind.__version__.count(".") == 2


def test_public_api_surface() -> None:
    for name in ("load", "Project", "Model", "RunResult", "RunError", "DAG", "UnwindError"):
        assert hasattr(unwind, name), name
