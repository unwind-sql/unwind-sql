"""Smoke tests for the package skeleton."""

from __future__ import annotations

import unwind


def test_version_is_exposed() -> None:
    assert isinstance(unwind.__version__, str)
    assert unwind.__version__.count(".") == 2


def test_public_api_surface() -> None:
    for name in (
        "load",
        "load_from_rows",
        "Project",
        "Model",
        "RunResult",
        "RunError",
        "DAG",
        "UnwindError",
    ):
        assert hasattr(unwind, name), name


def test_load_from_db_removed() -> None:
    assert not hasattr(unwind, "load_from_db")
