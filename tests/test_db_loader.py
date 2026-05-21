"""Tests for the database loader (uses in-memory SQLite via SQLAlchemy)."""

from __future__ import annotations

import pytest

import unwind
from unwind.errors import ProjectLoadError

sqlalchemy = pytest.importorskip("sqlalchemy")


def _seed(url: str, *, table: str = "sql_defs", rows: list[dict] | None = None) -> None:
    """Create `table` in the given SQLAlchemy URL and insert `rows`."""
    engine = sqlalchemy.create_engine(url)
    md = sqlalchemy.MetaData()
    sqlalchemy.Table(
        table,
        md,
        sqlalchemy.Column("name", sqlalchemy.String, primary_key=True),
        sqlalchemy.Column("sql", sqlalchemy.Text, nullable=False),
        sqlalchemy.Column("kind", sqlalchemy.String, nullable=True),
        sqlalchemy.Column("project", sqlalchemy.String, nullable=True),
    )
    md.create_all(engine)
    if rows:
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text(
                f"INSERT INTO {table}(name, sql, kind, project) "
                "VALUES (:name, :sql, :kind, :project)"
            ), rows)


def test_load_from_db_basic(tmp_path) -> None:
    url = f"sqlite:///{tmp_path}/db.sqlite"
    _seed(url, rows=[
        {"name": "stg_a", "sql": "SELECT 1 AS x;", "kind": "model", "project": "p1"},
        {"name": "stg_b", "sql": "SELECT 2 AS y FROM stg_a;", "kind": "model", "project": "p1"},
    ])
    project = unwind.load_from_db(url, "sql_defs", kind_column="kind")

    assert set(project.models) == {"stg_a", "stg_b"}
    assert project.macros == {}
    stg_a = project.models["stg_a"]
    assert isinstance(stg_a, unwind.Model)
    assert stg_a.raw_sql == "SELECT 1 AS x;"
    assert stg_a.path is None
    assert "stg_a" in stg_a.origin
    assert stg_a.materialized == "table"


def test_load_from_db_inline_directives(tmp_path) -> None:
    url = f"sqlite:///{tmp_path}/db.sqlite"
    _seed(url, rows=[
        {
            "name": "stg_x",
            "sql": "-- @group: ingestion\n-- @tags: a, b\nSELECT 1;",
            "kind": "model",
            "project": "p1",
        },
    ])
    project = unwind.load_from_db(url, "sql_defs", kind_column="kind")
    model = project.models["stg_x"]
    assert model.group == "ingestion"
    assert model.tags == ("a", "b")


def test_load_from_db_loads_macros(tmp_path) -> None:
    url = f"sqlite:///{tmp_path}/db.sqlite"
    _seed(url, rows=[
        {
            "name": "plus_one",
            "sql": "{% macro plus_one(col) %}({{ col }} + 1){% endmacro %}",
            "kind": "macro",
            "project": "p1",
        },
        {
            "name": "stg_x",
            "sql": "SELECT {{ plus_one('qty') }} AS qty_p1;",
            "kind": "model",
            "project": "p1",
        },
    ])
    project = unwind.load_from_db(url, "sql_defs", kind_column="kind")
    assert "plus_one" in project.macros
    rendered = project.render()
    stg_x = rendered.models["stg_x"]
    assert isinstance(stg_x, unwind.Model)
    assert stg_x.rendered_sql is not None
    assert "(qty + 1)" in stg_x.rendered_sql


def test_load_from_db_where_filter(tmp_path) -> None:
    url = f"sqlite:///{tmp_path}/db.sqlite"
    _seed(url, rows=[
        {"name": "stg_a", "sql": "SELECT 1;", "kind": "model", "project": "p1"},
        {"name": "stg_b", "sql": "SELECT 2;", "kind": "model", "project": "p2"},
    ])
    project = unwind.load_from_db(
        url, "sql_defs", kind_column="kind", where="project = 'p1'"
    )
    assert set(project.models) == {"stg_a"}


def test_load_from_db_no_kind_column(tmp_path) -> None:
    url = f"sqlite:///{tmp_path}/db.sqlite"
    _seed(url, rows=[
        {"name": "stg_a", "sql": "SELECT 1;", "kind": None, "project": None},
    ])
    project = unwind.load_from_db(url, "sql_defs")
    assert set(project.models) == {"stg_a"}


def test_load_from_db_custom_column_names(tmp_path) -> None:
    url = f"sqlite:///{tmp_path}/db.sqlite"
    engine = sqlalchemy.create_engine(url)
    md = sqlalchemy.MetaData()
    sqlalchemy.Table(
        "sql_defs",
        md,
        sqlalchemy.Column("model_name", sqlalchemy.String, primary_key=True),
        sqlalchemy.Column("sql_code", sqlalchemy.Text, nullable=False),
    )
    md.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text(
            "INSERT INTO sql_defs(model_name, sql_code) VALUES ('stg_a', 'SELECT 1;')"
        ))

    project = unwind.load_from_db(
        url, "sql_defs", name_column="model_name", sql_column="sql_code"
    )
    assert set(project.models) == {"stg_a"}


def test_load_from_db_rejects_duplicate_model(tmp_path) -> None:
    url = f"sqlite:///{tmp_path}/db.sqlite"
    # SQLite primary key prevents direct duplicate insert; use a non-PK seed.
    engine = sqlalchemy.create_engine(url)
    md = sqlalchemy.MetaData()
    sqlalchemy.Table(
        "sql_defs",
        md,
        sqlalchemy.Column("name", sqlalchemy.String, nullable=False),
        sqlalchemy.Column("sql", sqlalchemy.Text, nullable=False),
    )
    md.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text(
            "INSERT INTO sql_defs(name, sql) VALUES ('dup', 'SELECT 1;'), ('dup', 'SELECT 2;')"
        ))

    with pytest.raises(ProjectLoadError, match="duplicate model name 'dup'"):
        unwind.load_from_db(url, "sql_defs")


def test_load_from_db_rejects_missing_column(tmp_path) -> None:
    url = f"sqlite:///{tmp_path}/db.sqlite"
    _seed(url, rows=[{"name": "a", "sql": "SELECT 1;", "kind": None, "project": None}])
    with pytest.raises(ProjectLoadError, match="missing required column"):
        unwind.load_from_db(url, "sql_defs", sql_column="does_not_exist")


def test_load_from_db_rejects_empty_table(tmp_path) -> None:
    url = f"sqlite:///{tmp_path}/db.sqlite"
    _seed(url, rows=[])
    with pytest.raises(ProjectLoadError, match="no models found"):
        unwind.load_from_db(url, "sql_defs")


def test_load_from_db_rejects_unknown_table(tmp_path) -> None:
    url = f"sqlite:///{tmp_path}/db.sqlite"
    sqlalchemy.create_engine(url).connect().close()  # create empty DB file
    with pytest.raises(ProjectLoadError, match="could not reflect table"):
        unwind.load_from_db(url, "no_such_table")
