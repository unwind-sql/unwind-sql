"""Tests for the SQLGlot-backed dependency graph."""

from __future__ import annotations

from pathlib import Path

import pytest

import unwind
from unwind.dag import DAGError

EXAMPLE_RAW = {"raw_orders", "raw_shipments", "raw_refunds"}
EXAMPLE_REF = {"ref_carrier_rates", "ref_local_taxes"}
EXAMPLE_INT = {
    "int_order_base",
    "int_orders_dedup",
    "int_transport_costs",
    "int_tax_costs",
    "int_net_margin_per_order",
}
EXAMPLE_FCT = {"fct_warehouse_profitability"}
EXAMPLE_ALL = EXAMPLE_RAW | EXAMPLE_REF | EXAMPLE_INT | EXAMPLE_FCT


def test_example_project_dag(example_models_dir: Path) -> None:
    dag = unwind.load(example_models_dir).render().dag()

    assert set(dag.nodes) == EXAMPLE_ALL
    assert dag.sources == frozenset()  # everything is now a model

    order = list(dag.execution_order)
    # raw/ref leaves come first (any order between them), then int_, then fct_.
    assert set(order[: len(EXAMPLE_RAW | EXAMPLE_REF)]) == EXAMPLE_RAW | EXAMPLE_REF
    assert order.index("int_order_base") < order.index("int_orders_dedup")
    assert order.index("int_orders_dedup") < order.index("int_transport_costs")
    assert order.index("int_transport_costs") < order.index("int_tax_costs")
    assert order.index("int_tax_costs") < order.index("int_net_margin_per_order")
    assert order.index("int_net_margin_per_order") < order.index("fct_warehouse_profitability")


def test_node_dependencies(example_models_dir: Path) -> None:
    dag = unwind.load(example_models_dir).render().dag()

    raw_orders = dag.nodes["raw_orders"]
    assert raw_orders.depends_on_models == frozenset()
    assert raw_orders.depends_on_sources == frozenset()

    base = dag.nodes["int_order_base"]
    assert base.depends_on_models == frozenset(
        {"raw_orders", "raw_shipments", "ref_carrier_rates", "ref_local_taxes"}
    )
    assert base.depends_on_sources == frozenset()

    margin = dag.nodes["int_net_margin_per_order"]
    assert margin.depends_on_models == frozenset({"int_tax_costs", "raw_refunds"})

    fct = dag.nodes["fct_warehouse_profitability"]
    assert fct.depends_on_models == frozenset({"int_net_margin_per_order"})


def test_upstream_and_downstream(example_models_dir: Path) -> None:
    dag = unwind.load(example_models_dir).render().dag()

    assert dag.upstream("int_tax_costs") == frozenset(
        {
            "raw_orders",
            "raw_shipments",
            "ref_carrier_rates",
            "ref_local_taxes",
            "int_order_base",
            "int_orders_dedup",
            "int_transport_costs",
        }
    )
    assert dag.downstream("raw_refunds") == frozenset(
        {"int_net_margin_per_order", "fct_warehouse_profitability"}
    )
    assert dag.upstream("raw_orders") == frozenset()


def test_subdag(example_models_dir: Path) -> None:
    dag = unwind.load(example_models_dir).render().dag()
    sub = dag.subdag("int_tax_costs")

    assert set(sub.nodes) == {
        "raw_orders",
        "raw_shipments",
        "ref_carrier_rates",
        "ref_local_taxes",
        "int_order_base",
        "int_orders_dedup",
        "int_transport_costs",
        "int_tax_costs",
    }
    sub_order = list(sub.execution_order)
    assert sub_order[-1] == "int_tax_costs"
    assert sub_order.index("int_order_base") < sub_order.index("int_orders_dedup")
    assert sub_order.index("int_orders_dedup") < sub_order.index("int_transport_costs")


def test_unknown_model_raises(example_models_dir: Path) -> None:
    dag = unwind.load(example_models_dir).render().dag()
    with pytest.raises(DAGError, match="unknown model"):
        dag.upstream("does_not_exist")


def test_unrendered_project_rejected(example_models_dir: Path) -> None:
    project = unwind.load(example_models_dir)
    with pytest.raises(DAGError, match="not rendered"):
        project.dag()


def test_cte_local_names_excluded(tmp_path: Path) -> None:
    (tmp_path / "raw_orders.sql").write_text("SELECT 1 AS id;", encoding="utf-8")
    (tmp_path / "stg_orders.sql").write_text(
        """
        WITH cleaned AS (SELECT * FROM raw_orders)
        SELECT * FROM cleaned;
        """,
        encoding="utf-8",
    )
    dag = unwind.load(tmp_path).render().dag()

    stg = dag.nodes["stg_orders"]
    assert stg.depends_on_models == frozenset({"raw_orders"})
    assert "cleaned" not in stg.depends_on_models
    assert "cleaned" not in dag.sources


def test_external_source_when_table_unmodelled(tmp_path: Path) -> None:
    (tmp_path / "fct.sql").write_text(
        "SELECT * FROM information_schema.tables;",
        encoding="utf-8",
    )
    dag = unwind.load(tmp_path).render().dag()
    # `tables` is not a project model -> external source
    assert dag.sources == frozenset({"tables"})
    assert dag.nodes["fct"].depends_on_models == frozenset()


def test_cycle_detected(tmp_path: Path) -> None:
    (tmp_path / "a.sql").write_text("SELECT * FROM b;", encoding="utf-8")
    (tmp_path / "b.sql").write_text("SELECT * FROM a;", encoding="utf-8")
    project = unwind.load(tmp_path).render()
    with pytest.raises(DAGError, match="cycle detected"):
        project.dag()


def test_parse_error_surfaces(tmp_path: Path) -> None:
    (tmp_path / "broken.sql").write_text("THIS IS NOT SQL ((", encoding="utf-8")
    project = unwind.load(tmp_path).render()
    with pytest.raises(DAGError, match="failed to parse"):
        project.dag()
