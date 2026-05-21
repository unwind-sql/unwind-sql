"""Tests for table-level and column-level lineage."""

from __future__ import annotations

from pathlib import Path

import pytest

import unwind
from unwind.lineage import ColumnRef, LineageError

# ── Table lineage ───────────────────────────────────────────────────────────


def test_table_lineage_for_int_tax_costs(example_models_dir: Path) -> None:
    project = unwind.load(example_models_dir)
    lineage = project.get_table_lineage("int_tax_costs")

    assert lineage.target == "int_tax_costs"
    assert lineage.nodes == frozenset(
        {
            "raw_orders",
            "raw_shipments",
            "ref_carrier_rates",
            "ref_local_taxes",
            "int_order_base",
            "int_orders_dedup",
            "int_transport_costs",
            "int_tax_costs",
        }
    )
    assert ("int_order_base", "int_orders_dedup") in lineage.edges
    assert ("int_orders_dedup", "int_transport_costs") in lineage.edges
    assert ("int_transport_costs", "int_tax_costs") in lineage.edges
    assert ("raw_orders", "int_order_base") in lineage.edges
    # Downstream models should not appear
    assert "fct_warehouse_profitability" not in lineage.nodes
    assert "raw_refunds" not in lineage.nodes


def test_table_lineage_for_leaf_model(example_models_dir: Path) -> None:
    lineage = unwind.load(example_models_dir).get_table_lineage("raw_orders")
    assert lineage.nodes == frozenset({"raw_orders"})
    assert lineage.edges == frozenset()


def test_table_lineage_full_pipeline(example_models_dir: Path) -> None:
    lineage = unwind.load(example_models_dir).get_table_lineage("fct_warehouse_profitability")
    assert "fct_warehouse_profitability" in lineage.nodes
    assert "raw_refunds" in lineage.nodes
    assert ("int_net_margin_per_order", "fct_warehouse_profitability") in lineage.edges


def test_table_lineage_unknown_model(example_models_dir: Path) -> None:
    project = unwind.load(example_models_dir)
    with pytest.raises(LineageError, match="unknown model"):
        project.get_table_lineage("does_not_exist")


# ── Column lineage ──────────────────────────────────────────────────────────


def _all_names(node: ColumnRef) -> set[str]:
    """Flatten a ColumnRef tree into the set of all node names (uppercased by sqlglot)."""
    out = {node.name}
    for child in node.upstream:
        out |= _all_names(child)
    return out


def test_column_lineage_local_tax_fee(example_models_dir: Path) -> None:
    project = unwind.load(example_models_dir)
    col = project.get_column_lineage("int_tax_costs", column="local_tax_fee")

    assert "LOCAL_TAX_FEE" in col.name.upper()
    # The macro `apply_fee` expanded to ROUND(... COALESCE(...) ...)
    expr_upper = col.expression.upper()
    assert "ROUND" in expr_upper
    assert "COALESCE" in expr_upper

    # Tree must contain the upstream columns: gross_sales, tax_pct, fixed_handling_fee
    all_names_upper = {n.upper() for n in _all_names(col)}
    assert any("GROSS_SALES" in n for n in all_names_upper)
    assert any("TAX_PCT" in n for n in all_names_upper)
    assert any("FIXED_HANDLING_FEE" in n for n in all_names_upper)


def test_column_lineage_simple_passthrough(tmp_path: Path) -> None:
    (tmp_path / "src.sql").write_text(
        "SELECT id, name FROM (VALUES (1, 'a')) AS t(id, name);", encoding="utf-8"
    )
    (tmp_path / "stg.sql").write_text("SELECT id, name FROM src;", encoding="utf-8")
    (tmp_path / "fct.sql").write_text("SELECT id AS pk, name FROM stg;", encoding="utf-8")

    col = unwind.load(tmp_path).get_column_lineage("fct", column="pk")
    names = {n.upper() for n in _all_names(col)}
    # Lineage should mention `id` somewhere upstream
    assert any("ID" in n or "PK" in n for n in names)
    # And reach back to `src`
    assert any("SRC" in n for n in names)


def test_column_lineage_unknown_model(example_models_dir: Path) -> None:
    project = unwind.load(example_models_dir)
    with pytest.raises(LineageError, match="unknown model"):
        project.get_column_lineage("nope", column="x")


def test_column_lineage_unknown_column(example_models_dir: Path) -> None:
    project = unwind.load(example_models_dir)
    with pytest.raises(LineageError, match="column lineage failed"):
        project.get_column_lineage("int_tax_costs", column="totally_unknown_column")


def test_lineage_methods_auto_render(example_models_dir: Path) -> None:
    """Both lineage entry points must work on an unrendered project."""
    project = unwind.load(example_models_dir)
    assert all(
        getattr(m, "rendered_sql", None) is None for m in project.models.values()
    )

    project.get_table_lineage("int_tax_costs")
    project.get_column_lineage("int_tax_costs", column="local_tax_fee")

    # The original project should still be unrendered (no mutation).
    assert all(
        getattr(m, "rendered_sql", None) is None for m in project.models.values()
    )
