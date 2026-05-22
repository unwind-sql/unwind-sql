"""Tests for `load_from_rows` (the rows-in project loader)."""

from __future__ import annotations

import pytest

import unwind
from unwind.errors import ProjectLoadError


def test_load_from_rows_basic() -> None:
    project = unwind.load_from_rows(
        [
            {"name": "stg_a", "sql": "SELECT 1 AS x;", "kind": "model"},
            {"name": "stg_b", "sql": "SELECT 2 AS y FROM stg_a;", "kind": "model"},
        ]
    )

    assert set(project.models) == {"stg_a", "stg_b"}
    assert project.macros == {}
    stg_a = project.models["stg_a"]
    assert isinstance(stg_a, unwind.Model)
    assert stg_a.raw_sql == "SELECT 1 AS x;"
    assert stg_a.path is None
    assert "stg_a" in stg_a.origin
    assert stg_a.materialized == "table"


def test_load_from_rows_inline_directives() -> None:
    project = unwind.load_from_rows(
        [
            {
                "name": "stg_x",
                "sql": "-- @group: ingestion\n-- @tags: a, b\nSELECT 1;",
            }
        ]
    )
    model = project.models["stg_x"]
    assert model.group == "ingestion"
    assert model.tags == ("a", "b")


def test_load_from_rows_loads_macros() -> None:
    project = unwind.load_from_rows(
        [
            {
                "name": "plus_one",
                "sql": "{% macro plus_one(col) %}({{ col }} + 1){% endmacro %}",
                "kind": "macro",
            },
            {
                "name": "stg_x",
                "sql": "SELECT {{ plus_one('qty') }} AS qty_p1;",
                "kind": "model",
            },
        ]
    )
    assert "plus_one" in project.macros
    rendered = project.render()
    stg_x = rendered.models["stg_x"]
    assert isinstance(stg_x, unwind.Model)
    assert stg_x.rendered_sql is not None
    assert "(qty + 1)" in stg_x.rendered_sql


def test_load_from_rows_no_kind_key() -> None:
    project = unwind.load_from_rows(
        [{"name": "stg_a", "sql": "SELECT 1;"}], kind_key=None
    )
    assert set(project.models) == {"stg_a"}


def test_load_from_rows_custom_keys() -> None:
    project = unwind.load_from_rows(
        [{"model_name": "stg_a", "sql_code": "SELECT 1;"}],
        name_key="model_name",
        sql_key="sql_code",
        kind_key=None,
    )
    assert set(project.models) == {"stg_a"}


def test_load_from_rows_accepts_tuples() -> None:
    project = unwind.load_from_rows(
        [
            ("stg_a", "SELECT 1;", None),
            ("stg_b", "SELECT 2 FROM stg_a;", "model"),
            ("plus_one", "{% macro plus_one(c) %}{{c}}+1{% endmacro %}", "macro"),
        ]
    )
    assert set(project.models) == {"stg_a", "stg_b"}
    assert "plus_one" in project.macros


def test_load_from_rows_rejects_duplicate_model() -> None:
    with pytest.raises(ProjectLoadError, match="duplicate model name 'dup'"):
        unwind.load_from_rows(
            [
                {"name": "dup", "sql": "SELECT 1;"},
                {"name": "dup", "sql": "SELECT 2;"},
            ],
            kind_key=None,
        )


def test_load_from_rows_rejects_missing_sql_field() -> None:
    with pytest.raises(ProjectLoadError, match="missing 'sql' field"):
        unwind.load_from_rows([{"name": "a"}], kind_key=None)


def test_load_from_rows_rejects_empty_input() -> None:
    with pytest.raises(ProjectLoadError, match="no rows provided"):
        unwind.load_from_rows([])


def test_load_from_rows_rejects_empty_name() -> None:
    with pytest.raises(ProjectLoadError, match="empty or non-string name"):
        unwind.load_from_rows([{"name": "", "sql": "SELECT 1;"}], kind_key=None)


def test_load_from_rows_custom_origin() -> None:
    project = unwind.load_from_rows(
        [{"name": "stg_a", "sql": "SELECT 1;"}],
        kind_key=None,
        origin="warehouse.sql_defs",
    )
    assert project.models["stg_a"].origin == "warehouse.sql_defs#stg_a"
