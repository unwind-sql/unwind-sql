"""Tests for the DuckDB runner."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

import unwind
from unwind.runner import ExecutedModel, RunError

EXAMPLE_MODEL_COUNT = 10
EXAMPLE_ORDERS_ROW_COUNT = 11  # raw_orders has 11 rows (10 normal + 1 outlier)
EXAMPLE_FILTERED_ORDERS = 10  # int_order_base filters out qty == 0 (ORD-1009)


def test_run_full_example_pipeline(example_data_ready: Path) -> None:
    project = unwind.load(example_data_ready)
    result = project.run(engine="duckdb")

    assert len(result.executed) == EXAMPLE_MODEL_COUNT
    assert result.total_duration_s > 0
    assert all(isinstance(m, ExecutedModel) for m in result.executed)
    assert all(m.duration_s >= 0 for m in result.executed)

    by_name = {m.name: m for m in result.executed}
    assert by_name["raw_orders"].row_count == EXAMPLE_ORDERS_ROW_COUNT
    assert by_name["int_order_base"].row_count == EXAMPLE_FILTERED_ORDERS

    # Final fact table groups orders by warehouse: 3 warehouses in the fixture.
    assert by_name["fct_warehouse_profitability"].row_count == 3

    # Topological invariants: raw/ref ran before any int_, int_ before fct_.
    order = result.names
    assert order.index("raw_orders") < order.index("int_order_base")
    assert order.index("int_net_margin_per_order") < order.index("fct_warehouse_profitability")


def test_run_with_target_only_runs_subdag(example_data_ready: Path) -> None:
    result = unwind.load(example_data_ready).run(engine="duckdb", target="int_tax_costs")

    names = result.names
    assert "fct_warehouse_profitability" not in names
    assert "int_net_margin_per_order" not in names
    assert names[-1] == "int_tax_costs"
    assert {"raw_orders", "raw_shipments", "ref_carrier_rates", "ref_local_taxes"} <= set(names)


def test_run_persists_tables_to_database_file(example_data_ready: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "out.duckdb"
    unwind.load(example_data_ready).run(engine="duckdb", database=db_path)

    assert db_path.exists()
    with duckdb.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT total_orders FROM fct_warehouse_profitability ORDER BY warehouse_id"
        ).fetchall()
    assert sum(r[0] for r in rows) == EXAMPLE_FILTERED_ORDERS


def test_run_unsupported_engine_raises(tmp_project: Path) -> None:
    project = unwind.load(tmp_project)
    with pytest.raises(ValueError, match="unsupported engine"):
        project.run(engine="snowflake")


def test_run_failing_sql_wraps_in_run_error(tmp_path: Path) -> None:
    (tmp_path / "broken.sql").write_text("SELECT * FROM does_not_exist_anywhere", encoding="utf-8")
    project = unwind.load(tmp_path)
    with pytest.raises(RunError, match="broken"):
        project.run(engine="duckdb")


def test_run_simple_in_memory_pipeline(tmp_path: Path) -> None:
    (tmp_path / "src.sql").write_text(
        "SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) AS t(id, label);",
        encoding="utf-8",
    )
    (tmp_path / "doubled.sql").write_text(
        "SELECT id * 2 AS id, label FROM src;",
        encoding="utf-8",
    )
    result = unwind.load(tmp_path).run(engine="duckdb")

    assert result.names == ["src", "doubled"]
    by_name = {m.name: m.row_count for m in result.executed}
    assert by_name == {"src": 3, "doubled": 3}


def test_run_strips_trailing_semicolon(tmp_path: Path) -> None:
    """Models often end with `;` — runner must wrap them in `(...)` cleanly."""
    (tmp_path / "m.sql").write_text("SELECT 42 AS answer;\n", encoding="utf-8")
    result = unwind.load(tmp_path).run(engine="duckdb")
    assert result.executed[0].row_count == 1


def test_run_view_materialization_creates_view_not_table(tmp_path: Path) -> None:
    """A `@materialized: view` model must end up as a DuckDB VIEW, not a TABLE."""
    (tmp_path / "src.sql").write_text(
        "SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) AS t(id, label);",
        encoding="utf-8",
    )
    (tmp_path / "viewed.sql").write_text(
        "-- @materialized: view\nSELECT id * 10 AS x FROM src;\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "out.duckdb"
    unwind.load(tmp_path).run(engine="duckdb", database=db_path)

    with duckdb.connect(str(db_path)) as conn:
        kinds = dict(
            conn.execute(
                "SELECT table_name, table_type FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
        )
    assert kinds.get("src") == "BASE TABLE"
    assert kinds.get("viewed") == "VIEW"


def test_run_external_materialization_writes_parquet(tmp_path: Path) -> None:
    """`@materialized: external` writes a parquet file and exposes the data as a view."""
    out_path = tmp_path / "out" / "fct.parquet"
    (tmp_path / "fct.sql").write_text(
        "-- @materialized: external\n"
        f"-- @location: {out_path}\n"
        "SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) AS t(id, label);\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "out.duckdb"
    result = unwind.load(tmp_path).run(engine="duckdb", database=db_path)

    assert out_path.exists(), "external model must write to its location"
    by_name = {m.name: m.row_count for m in result.executed}
    assert by_name["fct"] == 3

    with duckdb.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT id, label FROM fct ORDER BY id").fetchall()
    assert rows == [(1, "a"), (2, "b"), (3, "c")]


def test_run_external_creates_parent_directories(tmp_path: Path) -> None:
    """The runner mkdir's the parent so nested @location paths just work."""
    out_path = tmp_path / "deep" / "nested" / "x.parquet"
    (tmp_path / "fct.sql").write_text(
        "-- @materialized: external\n"
        f"-- @location: {out_path}\n"
        "SELECT 42 AS answer;\n",
        encoding="utf-8",
    )
    unwind.load(tmp_path).run(engine="duckdb")
    assert out_path.exists()


def test_run_external_with_jinja_location(tmp_path: Path) -> None:
    """`@location` is rendered through Jinja, so `{{ project_root }}` resolves."""
    (tmp_path / "fct.sql").write_text(
        "-- @materialized: external\n"
        "-- @location: {{ project_root }}/out.parquet\n"
        "SELECT 1 AS x;\n",
        encoding="utf-8",
    )
    unwind.load(tmp_path).run(engine="duckdb")
    assert (tmp_path / "out.parquet").exists()
