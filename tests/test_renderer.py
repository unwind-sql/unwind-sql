"""Tests for the Jinja renderer (with shared macros and user vars)."""

from __future__ import annotations

from pathlib import Path

import pytest

import unwind
from unwind.errors import TemplateRenderError
from unwind.project import Model


def _sql(project: unwind.Project, name: str) -> Model:
    """Narrow the union — the tests in this file only deal with SQL models."""
    model = project.models[name]
    assert isinstance(model, Model)
    return model


def test_render_resolves_macros_and_vars(tmp_project: Path) -> None:
    project = unwind.load(tmp_project).render({"d_reporting": "2026-04-28"})

    stg = _sql(project, "stg_orders").rendered_sql
    fct = _sql(project, "fct_orders").rendered_sql

    assert stg is not None
    assert fct is not None
    assert "(qty + 1)" in stg
    assert "{{" not in stg
    assert "{%" not in stg
    assert "2026-04-28" in fct


def test_render_example_project(example_models_dir: Path) -> None:
    project = unwind.load(example_models_dir).render()

    rendered = _sql(project, "int_transport_costs").rendered_sql
    assert rendered is not None
    assert "{{" not in rendered
    assert "ROUND(" in rendered  # apply_fee macro expanded
    assert "fuel_surcharge_pct" in rendered


def test_render_strict_undefined_raises(tmp_project: Path) -> None:
    project = unwind.load(tmp_project)
    with pytest.raises(TemplateRenderError, match="fct_orders"):
        project.render()  # missing d_reporting


def test_render_returns_new_project_without_mutating_input(tmp_project: Path) -> None:
    project = unwind.load(tmp_project)
    rendered = project.render({"d_reporting": "2026-04-28"})

    assert rendered is not project
    # `tmp_project` only contains SQL models, so each `m` here is a SQL Model.
    assert all(m.rendered_sql is None for m in project.models.values() if isinstance(m, Model))
    assert all(
        m.rendered_sql is not None for m in rendered.models.values() if isinstance(m, Model)
    )


def test_project_root_builtin_is_injected(tmp_path: Path) -> None:
    (tmp_path / "raw_orders.sql").write_text(
        "SELECT * FROM read_parquet('{{ project_root }}/../data/raw_orders.parquet');\n",
        encoding="utf-8",
    )
    project = unwind.load(tmp_path).render()
    raw_orders = _sql(project, "raw_orders").rendered_sql
    assert raw_orders is not None
    assert "{{" not in raw_orders
    assert "raw_orders.parquet" in raw_orders
    assert tmp_path.resolve().as_posix() in raw_orders


def test_user_var_overrides_project_root(tmp_project: Path) -> None:
    (tmp_project / "with_root.sql").write_text(
        "SELECT '{{ project_root }}' AS p;\n", encoding="utf-8"
    )
    project = unwind.load(tmp_project).render(
        {"d_reporting": "2026-04-28", "project_root": "/custom/path"}
    )
    rendered = _sql(project, "with_root").rendered_sql
    assert rendered is not None
    assert "/custom/path" in rendered
