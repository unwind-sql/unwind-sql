"""Tests for the documentation generator (parser, build, render)."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

import unwind
from unwind.docs.parser import parse_column_descriptions

# ---------- parser: column / annotation extraction -----------------------


def test_parser_trailing_comment_on_aliased_column() -> None:
    sql = (
        "SELECT\n"
        "    customer_id,           -- unique customer id\n"
        "    SUM(amount) AS total   -- total HT in EUR\n"
        "FROM orders\n"
    )
    descriptions, annotations = parse_column_descriptions(sql)
    assert descriptions == {
        "customer_id": "unique customer id",
        "total": "total HT in EUR",
    }
    assert annotations == ()


def test_parser_floating_annotation_for_cte_comment() -> None:
    sql = (
        "WITH\n"
        "    -- active customers only\n"
        "    active AS (SELECT id FROM customers WHERE is_active)\n"
        "SELECT id FROM active\n"
    )
    _, annotations = parse_column_descriptions(sql)
    assert any("active customers only" in a.text for a in annotations)


def test_parser_ignores_select_star() -> None:
    sql = "SELECT *, x AS y -- trailing\nFROM t\n"
    descriptions, _ = parse_column_descriptions(sql)
    assert descriptions == {"y": "trailing"}


def test_parser_returns_empty_on_unparseable_sql() -> None:
    sql = "this is not sql at all -- pretend trailing\n"
    descriptions, annotations = parse_column_descriptions(sql)
    assert descriptions == {}
    # The whole line becomes a floating annotation since there's no column to attribute to.
    assert any("pretend trailing" in a.text for a in annotations)


def test_parser_handles_dash_dash_inside_string_literal() -> None:
    sql = "SELECT 'a--b' AS literal_col, x -- real comment\nFROM t\n"
    descriptions, _ = parse_column_descriptions(sql)
    # The `--` inside the string must not be mistaken for a comment.
    assert "x" in descriptions
    assert descriptions["x"] == "real comment"


# ---------- loader: description from header + python docstring -----------


def test_loader_captures_header_description(tmp_path: Path) -> None:
    (tmp_path / "stg_orders.sql").write_text(
        "-- Orders staging table.\n"
        "-- One row per order, deduplicated on order_id.\n"
        "-- @group: staging\n"
        "SELECT id FROM raw_orders;\n",
        encoding="utf-8",
    )
    model = unwind.load(tmp_path).models["stg_orders"]
    assert model.group == "staging"
    assert model.description == (
        "Orders staging table.\nOne row per order, deduplicated on order_id."
    )


def test_loader_returns_none_when_no_description(tmp_path: Path) -> None:
    (tmp_path / "stg_orders.sql").write_text(
        "-- @group: staging\nSELECT 1;\n", encoding="utf-8"
    )
    model = unwind.load(tmp_path).models["stg_orders"]
    assert model.description is None


def test_loader_captures_python_module_docstring(tmp_path: Path) -> None:
    (tmp_path / "raw_x.py").write_text(
        '"""Daily snapshot of x.\n\nLoaded via helpers."""\n'
        "def model(context):\n"
        "    return None\n",
        encoding="utf-8",
    )
    model = unwind.load(tmp_path).models["raw_x"]
    assert model.description == "Daily snapshot of x.\n\nLoaded via helpers."


# ---------- build_documentation -----------------------------------------


def _mini_project(tmp_path: Path) -> Path:
    (tmp_path / "stg_orders.sql").write_text(
        "-- Cleaned orders.\n"
        "SELECT\n"
        "    id AS order_id,           -- canonical order id\n"
        "    amount                    -- order amount in cents\n"
        "FROM raw_orders;\n",
        encoding="utf-8",
    )
    (tmp_path / "fct_orders.sql").write_text(
        "-- Final fact table.\n"
        "SELECT\n"
        "    order_id,\n"
        "    amount * 1.2 AS amount_ttc\n"
        "FROM stg_orders;\n",
        encoding="utf-8",
    )
    (tmp_path / "raw_orders.py").write_text(
        '"""Raw orders, loaded from a small fixture."""\n'
        "import pyarrow as pa\n"
        "def model(context):\n"
        "    return pa.table({'id': [1, 2, 3], 'amount': [100, None, 300]})\n",
        encoding="utf-8",
    )
    return tmp_path


def test_docs_without_connection_returns_descriptions_and_structure(tmp_path: Path) -> None:
    project = unwind.load(_mini_project(tmp_path))
    docs = project.docs()
    assert set(docs.models) == {"stg_orders", "fct_orders", "raw_orders"}

    stg = docs.models["stg_orders"]
    assert stg.description == "Cleaned orders."
    descriptions = {c.name: c.description for c in stg.columns}
    assert descriptions == {
        "order_id": "canonical order id",
        "amount": "order amount in cents",
    }
    assert stg.upstreams == ("raw_orders",)


def test_docs_with_connection_resolves_types_and_inherits_descriptions(
    tmp_path: Path,
) -> None:
    project = unwind.load(_mini_project(tmp_path))
    conn = duckdb.connect(":memory:")
    try:
        result = project.run(connection=conn, on_event=lambda _: None)
        docs = project.docs(connection=result.connection)

        stg = docs.models["stg_orders"]
        assert all(c.type is not None for c in stg.columns)

        fct = docs.models["fct_orders"]
        # `order_id` in fct has no native description; it should inherit
        # `canonical order id` from stg_orders.order_id.
        order_id_col = next(c for c in fct.columns if c.name == "order_id")
        assert order_id_col.description == "canonical order id"
        assert order_id_col.inherited_from == "stg_orders.order_id"
    finally:
        conn.close()


def test_docs_with_stats_emits_one_query_per_model(tmp_path: Path) -> None:
    project = unwind.load(_mini_project(tmp_path))
    conn = duckdb.connect(":memory:")
    try:
        result = project.run(connection=conn, on_event=lambda _: None)
        docs = project.docs(connection=result.connection, with_stats=True)

        stg = docs.models["stg_orders"]
        amount = next(c for c in stg.columns if c.name == "amount")
        assert amount.stats is not None
        assert amount.stats.row_count == 3
        # One of the three rows has a NULL amount.
        assert amount.stats.null_count == 1
        assert amount.stats.distinct_count == 2
    finally:
        conn.close()


# ---------- renderers ----------------------------------------------------


def test_markdown_render_contains_model_sections(tmp_path: Path) -> None:
    project = unwind.load(_mini_project(tmp_path))
    md = project.docs().to_markdown()
    assert "## stg_orders" in md
    assert "## fct_orders" in md
    assert "Cleaned orders." in md
    assert "canonical order id" in md
    assert "```sql" in md


def test_json_render_is_serialisable_and_includes_schema(tmp_path: Path) -> None:
    project = unwind.load(_mini_project(tmp_path))
    payload = project.docs().to_json()
    # Round-trip through json to make sure everything is JSON-safe.
    raw = json.dumps(payload, ensure_ascii=False)
    decoded = json.loads(raw)
    assert "_schema" in decoded
    assert "purpose" in decoded["_schema"]
    names = {m["name"] for m in decoded["models"]}
    assert names == {"stg_orders", "fct_orders", "raw_orders"}


# ---------- annotations --------------------------------------------------


def test_annotations_carry_line_numbers(tmp_path: Path) -> None:
    (tmp_path / "stg_x.sql").write_text(
        "SELECT\n"
        "    -- Step 1: pick the active rows\n"
        "    a,\n"
        "    b -- attached to column b\n"
        "FROM t\n"
        "WHERE c -- where clause note\n",
        encoding="utf-8",
    )
    project = unwind.load(tmp_path).render()
    model = project.models["stg_x"]
    assert not isinstance(model, unwind.PythonModel)
    descriptions, annotations = parse_column_descriptions(model.rendered_sql or "")
    assert descriptions == {"b": "attached to column b"}
    annotation_texts = {a.text for a in annotations}
    assert "Step 1: pick the active rows" in annotation_texts
    assert "where clause note" in annotation_texts


@pytest.mark.parametrize(
    ("name", "expected_kind"),
    [
        ("stg_orders", "sql"),
        ("fct_orders", "sql"),
        ("raw_orders", "python"),
    ],
)
def test_docs_model_kind_reflects_source(
    tmp_path: Path, name: str, expected_kind: str
) -> None:
    project = unwind.load(_mini_project(tmp_path))
    docs = project.docs()
    assert docs.models[name].kind == expected_kind
