"""Tests for forward column impact analysis (rename / type change)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

import unwind
from unwind.impact import ImpactError, get_column_impact


def _write(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")


# ── Basic propagation ──────────────────────────────────────────────────────


def test_impact_unknown_model_raises(tmp_path: Path) -> None:
    _write(tmp_path / "src.sql", "SELECT 1 AS x;\n")
    project = unwind.load(tmp_path)
    with pytest.raises(ImpactError, match="unknown model"):
        get_column_impact(project, "does_not_exist", "x")


def test_impact_unknown_column_raises(tmp_path: Path) -> None:
    _write(tmp_path / "src.sql", "SELECT 1 AS x;\n")
    project = unwind.load(tmp_path)
    with pytest.raises(ImpactError, match="unknown column"):
        get_column_impact(project, "src", "nope")


def test_impact_projection_chain(tmp_path: Path) -> None:
    """A column reused in successive projections propagates through the chain."""
    _write(tmp_path / "src.sql", "SELECT 1 AS x, 2 AS y;\n")
    _write(tmp_path / "mid.sql", "SELECT x, y * 2 AS doubled_y FROM src;\n")
    _write(tmp_path / "fct.sql", "SELECT x AS pk FROM mid;\n")

    project = unwind.load(tmp_path)
    imp = project.get_column_impact("src", column="x")

    affected = {(c.model, c.column) for c in imp.affected}
    assert ("mid", "x") in affected
    assert ("fct", "pk") in affected
    # Untouched columns must NOT appear.
    assert ("mid", "doubled_y") not in affected
    assert imp.opaque_consumers == ()


def test_impact_source_type_is_reported(tmp_path: Path) -> None:
    _write(tmp_path / "src.sql", "SELECT CAST(1 AS BIGINT) AS x;\n")
    project = unwind.load(tmp_path)
    imp = project.get_column_impact("src", column="x")
    assert "BIGINT" in imp.source_type.upper()


# ── Non-projection references ──────────────────────────────────────────────


def test_impact_detects_join_key_usage(tmp_path: Path) -> None:
    """A rename of a JOIN key must surface — even though sqlglot's value
    lineage would never report it."""
    _write(tmp_path / "lhs.sql", "SELECT * FROM (VALUES (1, 'a'), (2, 'b')) AS t(id, label);")
    _write(tmp_path / "rhs.sql", "SELECT * FROM (VALUES (1, 10), (2, 20)) AS t(id, v);")
    _write(
        tmp_path / "joined.sql",
        "SELECT l.label, r.v FROM lhs l JOIN rhs r ON l.id = r.id;\n",
    )

    project = unwind.load(tmp_path)
    imp = project.get_column_impact("lhs", column="id")

    edges = [(e.child_model, e.usage) for e in imp.edges]
    assert ("joined", "join") in edges


def test_impact_detects_where_filter(tmp_path: Path) -> None:
    _write(tmp_path / "src.sql", "SELECT * FROM (VALUES (1, 'a'), (2, 'b')) AS t(id, label);")
    _write(tmp_path / "filtered.sql", "SELECT label FROM src WHERE id > 0;\n")

    project = unwind.load(tmp_path)
    imp = project.get_column_impact("src", column="id")

    edges = {(e.child_model, e.usage) for e in imp.edges}
    assert ("filtered", "filter") in edges
    # `id` is not in the SELECT projection of `filtered`, so no projection edge.
    assert not any(e.usage == "projection" and e.child_model == "filtered" for e in imp.edges)


def test_impact_detects_group_by(tmp_path: Path) -> None:
    _write(tmp_path / "src.sql", "SELECT * FROM (VALUES (1, 10), (1, 20), (2, 30)) AS t(k, v);")
    _write(
        tmp_path / "agg.sql",
        "SELECT k, SUM(v) AS total FROM src GROUP BY k;\n",
    )

    project = unwind.load(tmp_path)
    imp = project.get_column_impact("src", column="k")

    edges = {(e.child_model, e.usage) for e in imp.edges}
    assert ("agg", "group") in edges
    # `k` is also in the projection of `agg`.
    assert ("agg", "projection") in edges


# ── Python opacity ─────────────────────────────────────────────────────────


def test_impact_python_consumer_marked_opaque(tmp_path: Path) -> None:
    _write(tmp_path / "src.sql", "SELECT 1 AS x, 2 AS y;\n")
    _write(
        tmp_path / "sink.py",
        """
        DEPENDS_ON = ("src",)

        def model(context):
            context.duckdb.execute("CREATE OR REPLACE TABLE sink AS SELECT x FROM src")
            return None
        """,
    )

    project = unwind.load(tmp_path)
    imp = project.get_column_impact("src", column="x")
    assert "sink" in imp.opaque_consumers
    # No projection edge into the Python model — we can't introspect its body.
    assert not any(
        e.child_model == "sink" and e.usage == "projection" for e in imp.edges
    )


def test_impact_through_python_source(tmp_path: Path) -> None:
    """When the source itself is a Python model, sqlglot still sees its
    columns as opaque leaves, so the downstream SQL chain is fully traced."""
    _write(
        tmp_path / "raw.py",
        """
        import pyarrow as pa

        def model(context):
            return pa.table({"x": [1, 2, 3]})
        """,
    )
    _write(tmp_path / "stg.sql", "SELECT x * 2 AS xx FROM raw;\n")

    project = unwind.load(tmp_path)
    imp = project.get_column_impact("raw", column="x")

    affected = {(c.model, c.column) for c in imp.affected}
    assert ("stg", "xx") in affected


# ── End-to-end on the example project ──────────────────────────────────────


def test_impact_on_example_gross_sales(example_models_dir: Path) -> None:
    """raw_orders.gross_sales should propagate to fct_warehouse_profitability."""
    project = unwind.load(example_models_dir)
    imp = project.get_column_impact("raw_orders", column="gross_sales")

    by_model = {c.model: c for c in imp.affected}
    assert "int_order_base" in by_model
    assert "int_transport_costs" in by_model
    assert "int_tax_costs" in by_model
    assert "int_net_margin_per_order" in by_model
    assert "fct_warehouse_profitability" in by_model

    # local_tax_fee should be affected even though it's not literally
    # `gross_sales` — gross_sales feeds into the ROUND() computation.
    assert ("int_tax_costs", "local_tax_fee") in {
        (c.model, c.column) for c in imp.affected
    }


def test_impact_on_example_warehouse_id_catches_join_and_group(
    example_models_dir: Path,
) -> None:
    """warehouse_id is a JOIN key AND a GROUP BY — both must be flagged."""
    project = unwind.load(example_models_dir)
    imp = project.get_column_impact("raw_orders", column="warehouse_id")

    usages = {(e.child_model, e.usage) for e in imp.edges}
    assert ("int_order_base", "join") in usages
    assert any(usage == "group" for (_, usage) in usages)
