"""Tests for the DuckDB runner."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import duckdb
import pytest

import unwind
from unwind._progress import RunEvent
from unwind.runner import ExecutedModel, RunError

EXAMPLE_MODEL_COUNT = 11
EXAMPLE_ORDERS_ROW_COUNT = 11  # raw_orders has 11 rows (10 normal + 1 outlier)
EXAMPLE_FILTERED_ORDERS = 10  # int_order_base filters out qty == 0 (ORD-1009)


def test_run_full_example_pipeline(example_data_ready: Path) -> None:
    project = unwind.load(example_data_ready)
    result = project.run()

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
    result = unwind.load(example_data_ready).run(target="int_tax_costs")

    names = result.names
    assert "fct_warehouse_profitability" not in names
    assert "int_net_margin_per_order" not in names
    assert names[-1] == "int_tax_costs"
    assert {"raw_orders", "raw_shipments", "ref_carrier_rates", "ref_local_taxes"} <= set(names)


def test_run_persists_tables_to_database_file(example_data_ready: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "out.duckdb"
    unwind.load(example_data_ready).run(database=db_path)

    assert db_path.exists()
    with duckdb.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT total_orders FROM fct_warehouse_profitability ORDER BY warehouse_id"
        ).fetchall()
    assert sum(r[0] for r in rows) == EXAMPLE_FILTERED_ORDERS


def test_run_with_external_connection_does_not_close_it(tmp_path: Path) -> None:
    """Caller-owned connections must survive `.run()` (no implicit close)."""
    (tmp_path / "m.sql").write_text("SELECT 42 AS answer;\n", encoding="utf-8")
    project = unwind.load(tmp_path)

    conn = duckdb.connect(":memory:")
    try:
        result = project.run(connection=conn)
        assert result.executed[0].row_count == 1
        row = conn.execute("SELECT answer FROM m").fetchone()
        assert row == (42,)
    finally:
        conn.close()


def test_run_failing_sql_wraps_in_run_error(tmp_path: Path) -> None:
    (tmp_path / "broken.sql").write_text("SELECT * FROM does_not_exist_anywhere", encoding="utf-8")
    project = unwind.load(tmp_path)
    with pytest.raises(RunError, match="broken"):
        project.run()


def test_run_simple_in_memory_pipeline(tmp_path: Path) -> None:
    (tmp_path / "src.sql").write_text(
        "SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) AS t(id, label);",
        encoding="utf-8",
    )
    (tmp_path / "doubled.sql").write_text(
        "SELECT id * 2 AS id, label FROM src;",
        encoding="utf-8",
    )
    result = unwind.load(tmp_path).run()

    assert result.names == ["src", "doubled"]
    by_name = {m.name: m.row_count for m in result.executed}
    assert by_name == {"src": 3, "doubled": 3}


def test_run_strips_trailing_semicolon(tmp_path: Path) -> None:
    """Models often end with `;` — runner must wrap them in `(...)` cleanly."""
    (tmp_path / "m.sql").write_text("SELECT 42 AS answer;\n", encoding="utf-8")
    result = unwind.load(tmp_path).run()
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
    unwind.load(tmp_path).run(database=db_path)

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
    result = unwind.load(tmp_path).run(database=db_path)

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
    unwind.load(tmp_path).run()
    assert out_path.exists()


def test_run_external_with_jinja_location(tmp_path: Path) -> None:
    """`@location` is rendered through Jinja, so `{{ project_root }}` resolves."""
    (tmp_path / "fct.sql").write_text(
        "-- @materialized: external\n"
        "-- @location: {{ project_root }}/out.parquet\n"
        "SELECT 1 AS x;\n",
        encoding="utf-8",
    )
    unwind.load(tmp_path).run()
    assert (tmp_path / "out.parquet").exists()


def test_run_disabled_model_bypasses_to_first_parent(tmp_path: Path) -> None:
    """Blender-style mute: disabled model is aliased to its (alphabetically) first parent."""
    (tmp_path / "src.sql").write_text(
        "SELECT * FROM (VALUES (1, 'a'), (2, 'b')) AS t(id, label);",
        encoding="utf-8",
    )
    # `middle` is muted. Its body would only emit `id`, but the bypass aliases
    # it to `src`, so children that SELECT `label` still work.
    (tmp_path / "middle.sql").write_text(
        "-- @disabled: true\nSELECT id FROM src;\n",
        encoding="utf-8",
    )
    (tmp_path / "leaf.sql").write_text(
        "SELECT id, label FROM middle;\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "out.duckdb"
    result = unwind.load(tmp_path).run(database=db_path)

    by_name = {m.name: m.row_count for m in result.executed}
    assert by_name == {"src": 2, "middle": 2, "leaf": 2}

    with duckdb.connect(str(db_path)) as conn:
        # `middle` keeps the `label` column because it forwarded `src`, not its own body.
        cols = {r[0] for r in conn.execute("DESCRIBE middle").fetchall()}
        assert cols == {"id", "label"}
        rows = conn.execute("SELECT * FROM leaf ORDER BY id").fetchall()
    assert rows == [(1, "a"), (2, "b")]


def test_run_disabled_chain_bypasses_through(tmp_path: Path) -> None:
    """Two disabled models in a row: leaf still resolves to the live source."""
    (tmp_path / "src.sql").write_text(
        "SELECT * FROM (VALUES (10), (20)) AS t(id);",
        encoding="utf-8",
    )
    (tmp_path / "mid1.sql").write_text(
        "-- @disabled: true\nSELECT id FROM src;\n",
        encoding="utf-8",
    )
    (tmp_path / "mid2.sql").write_text(
        "-- @disabled: true\nSELECT id FROM mid1;\n",
        encoding="utf-8",
    )
    (tmp_path / "leaf.sql").write_text(
        "SELECT id FROM mid2;\n", encoding="utf-8"
    )
    result = unwind.load(tmp_path).run()
    by_name = {m.name: m.row_count for m in result.executed}
    assert by_name == {"src": 2, "mid1": 2, "mid2": 2, "leaf": 2}


def test_run_disabled_python_model_is_not_called(tmp_path: Path) -> None:
    """Python model with DISABLED = True must not have its function invoked."""
    (tmp_path / "raw_seed.sql").write_text(
        "SELECT * FROM (VALUES (1, 'a')) AS t(id, label);",
        encoding="utf-8",
    )
    (tmp_path / "py_model.py").write_text(
        "DISABLED = True\n"
        "DEPENDS_ON = ('raw_seed',)\n"
        "def model(context):\n"
        "    raise AssertionError('disabled python model must not run')\n",
        encoding="utf-8",
    )
    (tmp_path / "leaf.sql").write_text(
        "SELECT id FROM py_model;\n", encoding="utf-8"
    )
    result = unwind.load(tmp_path).run()
    by_name = {m.name: m.row_count for m in result.executed}
    assert by_name == {"raw_seed": 1, "py_model": 1, "leaf": 1}


def test_run_disabled_leaf_without_parents_is_skipped(tmp_path: Path) -> None:
    """Disabling a parent-less leaf leaves nothing materialised; downstream fails clearly."""
    (tmp_path / "src.sql").write_text(
        "-- @disabled: true\nSELECT 1 AS id;\n", encoding="utf-8"
    )
    (tmp_path / "leaf.sql").write_text(
        "SELECT id FROM src;\n", encoding="utf-8"
    )
    with pytest.raises(RunError, match="leaf"):
        unwind.load(tmp_path).run()


# ---------------------------------------------------------------------------
# Parallel execution + progress events
# ---------------------------------------------------------------------------


def test_run_workers_invalid_raises(tmp_path: Path) -> None:
    (tmp_path / "m.sql").write_text("SELECT 1 AS x", encoding="utf-8")
    project = unwind.load(tmp_path)
    with pytest.raises(ValueError, match="workers must be >= 1"):
        project.run(workers=0)


def test_run_workers_parallel_respects_topology(example_data_ready: Path) -> None:
    """workers>1 must still respect deps: int_ never lands before its raw_ parents."""
    result = unwind.load(example_data_ready).run(workers=4)
    assert len(result.executed) == EXAMPLE_MODEL_COUNT

    finish_index = {m.name: i for i, m in enumerate(result.executed)}
    assert finish_index["raw_orders"] < finish_index["int_order_base"]
    assert finish_index["int_order_base"] < finish_index["int_transport_costs"]
    assert finish_index["int_net_margin_per_order"] < finish_index[
        "fct_warehouse_profitability"
    ]


def test_run_workers_parallel_actually_overlaps(tmp_path: Path) -> None:
    """Two independent slow Python models run concurrently with workers=2.

    Sequential wall-clock would be ~2 * sleep_s; parallel approaches sleep_s.
    """
    sleep_s = 0.25
    (tmp_path / "raw_seed.sql").write_text(
        "SELECT * FROM (VALUES (1)) AS t(id);", encoding="utf-8"
    )
    for name in ("slow_a", "slow_b"):
        (tmp_path / f"{name}.py").write_text(
            "import time\n"
            "DEPENDS_ON = ('raw_seed',)\n"
            f"def model(context):\n"
            f"    time.sleep({sleep_s})\n"
            f"    return 'SELECT * FROM raw_seed'\n",
            encoding="utf-8",
        )
    project = unwind.load(tmp_path)

    t0 = time.perf_counter()
    project.run(workers=2)
    parallel_elapsed = time.perf_counter() - t0

    # Allow generous slack (DuckDB DDL serialization, thread startup) but still
    # well under the sequential floor of 2 * sleep_s.
    assert parallel_elapsed < 1.6 * sleep_s, (
        f"parallel run took {parallel_elapsed:.3f}s, expected ~{sleep_s:.2f}s"
    )


def test_run_workers_serializes_dependent_models(tmp_path: Path) -> None:
    """A dependency chain cannot overlap, even with `workers=4`."""
    (tmp_path / "a.sql").write_text("SELECT 1 AS x", encoding="utf-8")
    (tmp_path / "b.sql").write_text("SELECT x + 1 AS x FROM a", encoding="utf-8")
    (tmp_path / "c.sql").write_text("SELECT x + 1 AS x FROM b", encoding="utf-8")

    result = unwind.load(tmp_path).run(workers=4)
    order = [m.name for m in result.executed]
    assert order.index("a") < order.index("b") < order.index("c")


def test_run_emits_progress_events_sequential(tmp_path: Path) -> None:
    """workers=1: one model_start + model_done per node, bracketed by start/done."""
    (tmp_path / "a.sql").write_text("SELECT 1 AS x", encoding="utf-8")
    (tmp_path / "b.sql").write_text("SELECT x + 1 AS x FROM a", encoding="utf-8")

    events: list[RunEvent] = []
    unwind.load(tmp_path).run(on_event=events.append)

    kinds = [e.kind for e in events]
    assert kinds[0] == "start"
    assert kinds[-1] == "done"
    assert kinds.count("model_start") == 2
    assert kinds.count("model_done") == 2

    # Names appear in topological order, with totals/completed monotonic.
    started = [e.name for e in events if e.kind == "model_start"]
    done = [e.name for e in events if e.kind == "model_done"]
    assert started == ["a", "b"]
    assert done == ["a", "b"]

    final = events[-1]
    assert final.completed == final.total == 2


def test_run_emits_progress_events_parallel(tmp_path: Path) -> None:
    """workers=N: in_flight reports concurrent models; counts stay monotonic."""
    (tmp_path / "raw_a.sql").write_text("SELECT 1 AS x", encoding="utf-8")
    (tmp_path / "raw_b.sql").write_text("SELECT 2 AS x", encoding="utf-8")
    (tmp_path / "raw_c.sql").write_text("SELECT 3 AS x", encoding="utf-8")
    (tmp_path / "leaf.sql").write_text(
        "SELECT x FROM raw_a UNION ALL SELECT x FROM raw_b UNION ALL SELECT x FROM raw_c",
        encoding="utf-8",
    )

    events: list[RunEvent] = []
    lock = threading.Lock()

    def record(event: RunEvent) -> None:
        with lock:
            events.append(event)

    unwind.load(tmp_path).run(workers=3, on_event=record)

    kinds = [e.kind for e in events]
    assert kinds[0] == "start"
    assert kinds[-1] == "done"
    assert kinds.count("model_done") == 4

    # `completed` is non-decreasing across the event stream.
    completed = [e.completed for e in events]
    assert completed == sorted(completed)

    # At least once during the run, the three raw_* models are in flight simultaneously.
    max_in_flight = max(len(e.in_flight) for e in events)
    assert max_in_flight >= 2  # 3 is ideal, but 2 is the floor we can reliably assert


def test_run_progress_silenced_with_noop_callback(tmp_path: Path, capsys) -> None:
    """Passing `on_event=lambda _: None` mutes auto-progress even on a TTY."""
    (tmp_path / "m.sql").write_text("SELECT 1 AS x", encoding="utf-8")
    unwind.load(tmp_path).run(on_event=lambda _: None)
    captured = capsys.readouterr()
    # Auto-progress writes to stderr; explicit noop callback should preempt it.
    assert "running models" not in captured.err
    assert "running models" not in captured.out


def test_run_parallel_failure_aborts_run(tmp_path: Path) -> None:
    """A failing model raises RunError; the run aborts, doesn't deadlock."""
    (tmp_path / "good.sql").write_text("SELECT 1 AS x", encoding="utf-8")
    (tmp_path / "bad.sql").write_text(
        "SELECT * FROM definitely_does_not_exist", encoding="utf-8"
    )
    with pytest.raises(RunError, match="bad"):
        unwind.load(tmp_path).run(workers=2)
