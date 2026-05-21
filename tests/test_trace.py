"""Tests for deterministic value lineage."""

from __future__ import annotations

from pathlib import Path

import pytest

import unwind
from unwind.trace import TraceError, TraceNode, TraceResult


def _flatten(node: TraceNode) -> list[TraceNode]:
    out = [node]
    for child in node.upstream:
        out.extend(_flatten(child))
    return out


def _by_ref(node: TraceNode) -> dict[tuple[str, str], TraceNode]:
    """Index every TraceNode in the tree by (model, column). Last wins on duplicates."""
    return {(n.model, n.column): n for n in _flatten(node)}


# ── Happy path on the example pipeline ──────────────────────────────────────


def test_trace_local_tax_fee_for_ord_7892(example_data_ready: Path) -> None:
    """ORD-7892: warehouse=WH-PARIS-SUD, gross_sales=500.

    local_tax_fee = ROUND(500 * 0.20 + 2.50, 2) = 102.50
    """
    project = unwind.load(example_data_ready)
    result = project.trace_value(
        model="int_tax_costs",
        column="local_tax_fee",
        where={"order_id": "ORD-7892"},
    )

    assert isinstance(result, TraceResult)
    assert result.model == "int_tax_costs"
    assert result.column == "local_tax_fee"
    assert result.where == {"order_id": "ORD-7892"}
    assert result.root.values == (102.50,)
    assert "ROUND" in result.root.expression.upper()
    assert result.root.upstream  # has upstream contributors

    by_ref = _by_ref(result.root)

    # Direct contributors of local_tax_fee (at int_transport_costs level)
    gross_sales = by_ref[("int_transport_costs", "gross_sales")]
    assert gross_sales.values == (500.0,)

    tax_pct = by_ref[("int_transport_costs", "tax_pct")]
    assert tax_pct.values == (0.20,)

    fixed_handling = by_ref[("int_transport_costs", "fixed_handling_fee")]
    assert fixed_handling.values == (2.50,)


def test_trace_reaches_raw_sources(example_data_ready: Path) -> None:
    """The trace must remount to raw_orders and ref_local_taxes."""
    result = unwind.load(example_data_ready).trace_value(
        model="int_tax_costs",
        column="local_tax_fee",
        where={"order_id": "ORD-7892"},
    )
    by_ref = _by_ref(result.root)

    raw_gross = by_ref.get(("raw_orders", "gross_sales"))
    assert raw_gross is not None
    assert raw_gross.values == (500.0,)
    # raw_orders has order_id directly → predicate applied without fallback
    assert raw_gross.predicate == {"order_id": "ORD-7892"}

    ref_tax = by_ref.get(("ref_local_taxes", "tax_pct"))
    assert ref_tax is not None
    assert ref_tax.values == (0.20,)
    # ref_local_taxes has no `order_id` → fallback to target with target predicate
    assert ref_tax.predicate == {"order_id": "ORD-7892"}


def test_trace_fuel_surcharge_with_outlier(example_data_ready: Path) -> None:
    """ORD-7892 has weight_kg=1500 (outlier vs typical ~1.5).

    The trace must surface this absurd value at raw_shipments.
    """
    result = unwind.load(example_data_ready).trace_value(
        model="int_tax_costs",
        column="fuel_surcharge_fee",
        where={"order_id": "ORD-7892"},
    )
    by_ref = _by_ref(result.root)
    raw_weight = by_ref.get(("raw_shipments", "weight_kg"))
    assert raw_weight is not None
    assert raw_weight.values == (1500.0,)


def test_trace_aggregate_returns_underlying_rows(example_data_ready: Path) -> None:
    """fct_warehouse_profitability.total_revenue for WH-PARIS-SUD aggregates 4 orders."""
    result = unwind.load(example_data_ready).trace_value(
        model="fct_warehouse_profitability",
        column="total_revenue",
        where={"warehouse_id": "WH-PARIS-SUD"},
    )

    assert result.root.values == (pytest.approx(250.0 + 120.0 + 175.0 + 500.0),)
    by_ref = _by_ref(result.root)
    # gross_sales upstream now has 4 contributing rows (one per order)
    gross = by_ref.get(("int_net_margin_per_order", "gross_sales"))
    assert gross is not None
    assert sorted(gross.values) == [120.0, 175.0, 250.0, 500.0]


# ── Errors and edge cases ───────────────────────────────────────────────────


def test_trace_unknown_model(example_data_ready: Path) -> None:
    project = unwind.load(example_data_ready)
    with pytest.raises(TraceError, match="unknown model"):
        project.trace_value(model="does_not_exist", column="x", where={"order_id": "ORD-7892"})


def test_trace_predicate_column_not_in_target(example_data_ready: Path) -> None:
    project = unwind.load(example_data_ready)
    with pytest.raises(TraceError, match="predicate columns not in"):
        project.trace_value(
            model="int_tax_costs",
            column="local_tax_fee",
            where={"nonexistent_column": "X"},
        )


def test_trace_empty_where_rejected(example_data_ready: Path) -> None:
    project = unwind.load(example_data_ready)
    with pytest.raises(TraceError, match="must contain at least"):
        project.trace_value(model="int_tax_costs", column="local_tax_fee", where={})


def test_trace_no_match_returns_empty_values(example_data_ready: Path) -> None:
    result = unwind.load(example_data_ready).trace_value(
        model="int_tax_costs",
        column="local_tax_fee",
        where={"order_id": "DOES-NOT-EXIST"},
    )
    assert result.root.values == ()


def test_trace_depth_limits_recursion(example_data_ready: Path) -> None:
    project = unwind.load(example_data_ready)
    deep = project.trace_value(
        model="int_tax_costs",
        column="local_tax_fee",
        where={"order_id": "ORD-7892"},
    )
    shallow = project.trace_value(
        model="int_tax_costs",
        column="local_tax_fee",
        where={"order_id": "ORD-7892"},
        depth=1,
    )
    # depth=1 should cut off the recursion strictly earlier
    assert len(_flatten(shallow.root)) < len(_flatten(deep.root))
    # depth=0 stops at the root
    zero = project.trace_value(
        model="int_tax_costs",
        column="local_tax_fee",
        where={"order_id": "ORD-7892"},
        depth=0,
    )
    assert zero.root.upstream == ()


def test_trace_predicate_case_insensitive_keys(example_data_ready: Path) -> None:
    """User-provided key with arbitrary case must match the actual column."""
    result = unwind.load(example_data_ready).trace_value(
        model="int_tax_costs",
        column="local_tax_fee",
        where={"Order_Id": "ORD-7892"},
    )
    assert result.root.values == (102.50,)


# ── Formula + substitution ──────────────────────────────────────────────────


def test_trace_substitution_for_local_tax_fee(example_data_ready: Path) -> None:
    """Root substitution must inline gross_sales=500.0, tax_pct=0.2, fee=2.5."""
    result = unwind.load(example_data_ready).trace_value(
        model="int_tax_costs",
        column="local_tax_fee",
        where={"order_id": "ORD-7892"},
    )
    formula = result.root.expression
    substituted = result.root.substituted

    # Formula keeps qualified column references and a ROUND(...) wrapper.
    assert "ROUND" in formula
    assert "gross_sales" in formula
    assert "tax_pct" in formula

    # Substituted has every reference replaced by a scalar.
    assert "ROUND" in substituted
    assert "500.0" in substituted
    assert "0.2" in substituted
    assert "2.5" in substituted
    # No leftover qualified references in substituted form.
    assert "int_transport_costs." not in substituted
    assert "gross_sales" not in substituted


def test_trace_strips_alias_and_comments(example_data_ready: Path) -> None:
    """The expression must not carry `AS local_tax_fee` or the macro's `/* ... */` comment."""
    result = unwind.load(example_data_ready).trace_value(
        model="int_tax_costs",
        column="local_tax_fee",
        where={"order_id": "ORD-7892"},
    )
    assert " AS " not in result.root.expression
    assert "/*" not in result.root.expression
    assert " AS " not in result.root.substituted
    assert "/*" not in result.root.substituted


def test_trace_substitution_for_aggregate(example_data_ready: Path) -> None:
    """`SUM(int_net_margin_per_order.gross_sales)` must show all 4 values."""
    result = unwind.load(example_data_ready).trace_value(
        model="fct_warehouse_profitability",
        column="total_revenue",
        where={"warehouse_id": "WH-PARIS-SUD"},
    )
    sub = result.root.substituted
    assert sub.startswith("SUM([")
    assert sub.endswith("])")
    for value in (500.0, 175.0, 120.0, 250.0):
        assert str(value) in sub
    assert "...+" not in sub  # 4 values fit under default max=5


def test_trace_substitution_truncates_to_max_values(example_data_ready: Path) -> None:
    result = unwind.load(example_data_ready).trace_value(
        model="fct_warehouse_profitability",
        column="total_revenue",
        where={"warehouse_id": "WH-PARIS-SUD"},
        max_values=2,
    )
    sub = result.root.substituted
    assert sub.startswith("SUM([")
    assert sub.endswith("])")
    assert "...+2" in sub  # 4 values, kept 2, overflow 2


def test_trace_value_count_reflects_truncation(example_data_ready: Path) -> None:
    """When `max_values < total contributing rows`, `value_count` exposes the true count."""
    result = unwind.load(example_data_ready).trace_value(
        model="fct_warehouse_profitability",
        column="total_revenue",
        where={"warehouse_id": "WH-PARIS-SUD"},
        max_values=2,
    )
    by_ref = _by_ref(result.root)
    gross = by_ref[("int_net_margin_per_order", "gross_sales")]
    assert gross.value_count == 4  # 4 PARIS-SUD rows after qty>0 filter
    assert len(gross.values) <= 3  # SQL fetch capped at max_values + 1


def test_trace_value_count_matches_when_under_limit(example_data_ready: Path) -> None:
    """When the row count fits under `max_values`, `value_count == len(values)`."""
    result = unwind.load(example_data_ready).trace_value(
        model="fct_warehouse_profitability",
        column="total_revenue",
        where={"warehouse_id": "WH-PARIS-SUD"},
        max_values=10,
    )
    by_ref = _by_ref(result.root)
    gross = by_ref[("int_net_margin_per_order", "gross_sales")]
    assert gross.value_count == 4
    assert len(gross.values) == 4


def test_trace_substitution_terminal_leaf_inlines_self_value(
    example_data_ready: Path,
) -> None:
    """A `raw_orders.gross_sales` leaf has no upstream; substituted is its own value."""
    result = unwind.load(example_data_ready).trace_value(
        model="int_tax_costs",
        column="local_tax_fee",
        where={"order_id": "ORD-7892"},
    )

    def find(node: TraceNode, model: str, column: str) -> TraceNode | None:
        if node.model == model and node.column == column:
            return node
        for c in node.upstream:
            hit = find(c, model, column)
            if hit is not None:
                return hit
        return None

    leaf = find(result.root, "raw_orders", "gross_sales")
    assert leaf is not None
    assert leaf.expression == "gross_sales"
    assert leaf.substituted == "500.0"
