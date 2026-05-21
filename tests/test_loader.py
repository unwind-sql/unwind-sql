"""Tests for the filesystem loader."""

from __future__ import annotations

from pathlib import Path

import pytest

import unwind
from unwind.errors import ProjectLoadError
from unwind.project import PythonModel

EXAMPLE_MODELS = {
    "raw_orders",
    "raw_shipments",
    "raw_refunds",
    "ref_carrier_rates",
    "ref_local_taxes",
    "int_order_base",
    "int_orders_dedup",
    "int_transport_costs",
    "int_tax_costs",
    "int_net_margin_per_order",
    "fct_warehouse_profitability",
}


def test_load_example_project(example_models_dir: Path) -> None:
    project = unwind.load(example_models_dir)

    assert set(project.models) == EXAMPLE_MODELS
    assert "apply_fee" in project.macros
    assert project.root == example_models_dir.resolve()

    assert project.models.get("apply_fee") is None, "macros/ files must not be registered as models"

    for model in project.models.values():
        if isinstance(model, PythonModel):
            assert callable(model.func)
        else:
            assert model.raw_sql.strip()
            assert model.rendered_sql is None


def test_load_minimal_project(tmp_project: Path) -> None:
    project = unwind.load(tmp_project)
    assert set(project.models) == {"stg_orders", "fct_orders"}


def test_load_missing_path(tmp_path: Path) -> None:
    with pytest.raises(ProjectLoadError, match="does not exist"):
        unwind.load(tmp_path / "nope")


def test_load_not_a_directory(tmp_path: Path) -> None:
    file_path = tmp_path / "lonely.sql"
    file_path.write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(ProjectLoadError, match="not a directory"):
        unwind.load(file_path)


def test_load_empty_directory(tmp_path: Path) -> None:
    with pytest.raises(ProjectLoadError, match=r"no `\.sql` or `\.py` models"):
        unwind.load(tmp_path)


def test_load_rejects_duplicate_model_names(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "orders.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "b" / "orders.sql").write_text("SELECT 2;", encoding="utf-8")

    with pytest.raises(ProjectLoadError, match="duplicate model name 'orders'"):
        unwind.load(tmp_path)


def test_load_without_macros_dir(tmp_path: Path) -> None:
    (tmp_path / "model.sql").write_text("SELECT 1;", encoding="utf-8")
    project = unwind.load(tmp_path)
    assert project.macros == {}


def test_load_parses_group_and_tags(tmp_path: Path) -> None:
    (tmp_path / "stg_x.sql").write_text(
        "-- @group: ingestion\n-- @tags: a, b, c\nSELECT 1;\n",
        encoding="utf-8",
    )
    project = unwind.load(tmp_path)
    model = project.models["stg_x"]
    assert model.group == "ingestion"
    assert model.tags == ("a", "b", "c")


def test_load_skips_plain_comments_before_directives(tmp_path: Path) -> None:
    (tmp_path / "stg_x.sql").write_text(
        "-- some plain comment\n\n-- @group: foo\nSELECT 1;\n",
        encoding="utf-8",
    )
    model = unwind.load(tmp_path).models["stg_x"]
    assert model.group == "foo"


def test_load_stops_parsing_at_first_sql_line(tmp_path: Path) -> None:
    (tmp_path / "stg_x.sql").write_text(
        "SELECT 1;\n-- @group: too_late\n",
        encoding="utf-8",
    )
    model = unwind.load(tmp_path).models["stg_x"]
    assert model.group is None


def test_load_no_metadata(tmp_path: Path) -> None:
    (tmp_path / "stg_x.sql").write_text("SELECT 1;\n", encoding="utf-8")
    model = unwind.load(tmp_path).models["stg_x"]
    assert model.group is None
    assert model.tags == ()


def test_load_rejects_duplicate_group_directive(tmp_path: Path) -> None:
    (tmp_path / "stg_x.sql").write_text(
        "-- @group: a\n-- @group: b\nSELECT 1;\n",
        encoding="utf-8",
    )
    with pytest.raises(ProjectLoadError, match="duplicate '@group'"):
        unwind.load(tmp_path)


def test_load_rejects_empty_group_value(tmp_path: Path) -> None:
    (tmp_path / "stg_x.sql").write_text("-- @group:\nSELECT 1;\n", encoding="utf-8")
    with pytest.raises(ProjectLoadError, match="empty '@group'"):
        unwind.load(tmp_path)


def test_load_default_materialization_is_table(tmp_path: Path) -> None:
    (tmp_path / "stg_x.sql").write_text("SELECT 1;\n", encoding="utf-8")
    model = unwind.load(tmp_path).models["stg_x"]
    assert model.materialized == "table"


def test_load_parses_view_materialization(tmp_path: Path) -> None:
    (tmp_path / "stg_x.sql").write_text(
        "-- @materialized: view\nSELECT 1;\n", encoding="utf-8"
    )
    model = unwind.load(tmp_path).models["stg_x"]
    assert model.materialized == "view"


def test_load_rejects_invalid_materialization(tmp_path: Path) -> None:
    (tmp_path / "stg_x.sql").write_text(
        "-- @materialized: cte\nSELECT 1;\n", encoding="utf-8"
    )
    with pytest.raises(ProjectLoadError, match="invalid '@materialized'"):
        unwind.load(tmp_path)


def test_load_parses_external_with_location(tmp_path: Path) -> None:
    (tmp_path / "fct_x.sql").write_text(
        "-- @materialized: external\n-- @location: out/x.parquet\nSELECT 1;\n",
        encoding="utf-8",
    )
    model = unwind.load(tmp_path).models["fct_x"]
    assert model.materialized == "external"
    assert model.location == "out/x.parquet"


def test_load_rejects_external_without_location(tmp_path: Path) -> None:
    (tmp_path / "fct_x.sql").write_text(
        "-- @materialized: external\nSELECT 1;\n", encoding="utf-8"
    )
    with pytest.raises(ProjectLoadError, match="requires '@location'"):
        unwind.load(tmp_path)


def test_load_rejects_location_without_external(tmp_path: Path) -> None:
    (tmp_path / "fct_x.sql").write_text(
        "-- @location: out/x.parquet\nSELECT 1;\n", encoding="utf-8"
    )
    with pytest.raises(ProjectLoadError, match="only valid with '@materialized: external'"):
        unwind.load(tmp_path)


def test_load_rejects_duplicate_location(tmp_path: Path) -> None:
    (tmp_path / "fct_x.sql").write_text(
        "-- @materialized: external\n"
        "-- @location: a.parquet\n"
        "-- @location: b.parquet\n"
        "SELECT 1;\n",
        encoding="utf-8",
    )
    with pytest.raises(ProjectLoadError, match="duplicate '@location'"):
        unwind.load(tmp_path)


def test_load_default_disabled_is_false(tmp_path: Path) -> None:
    (tmp_path / "stg_x.sql").write_text("SELECT 1;\n", encoding="utf-8")
    model = unwind.load(tmp_path).models["stg_x"]
    assert model.disabled is False


def test_load_parses_disabled_true(tmp_path: Path) -> None:
    (tmp_path / "stg_x.sql").write_text(
        "-- @disabled: true\nSELECT 1;\n", encoding="utf-8"
    )
    assert unwind.load(tmp_path).models["stg_x"].disabled is True


def test_load_parses_disabled_false(tmp_path: Path) -> None:
    (tmp_path / "stg_x.sql").write_text(
        "-- @disabled: false\nSELECT 1;\n", encoding="utf-8"
    )
    assert unwind.load(tmp_path).models["stg_x"].disabled is False


def test_load_rejects_invalid_disabled_value(tmp_path: Path) -> None:
    (tmp_path / "stg_x.sql").write_text(
        "-- @disabled: maybe\nSELECT 1;\n", encoding="utf-8"
    )
    with pytest.raises(ProjectLoadError, match="invalid '@disabled'"):
        unwind.load(tmp_path)


def test_load_rejects_duplicate_disabled(tmp_path: Path) -> None:
    (tmp_path / "stg_x.sql").write_text(
        "-- @disabled: true\n-- @disabled: false\nSELECT 1;\n",
        encoding="utf-8",
    )
    with pytest.raises(ProjectLoadError, match="duplicate '@disabled'"):
        unwind.load(tmp_path)


def test_load_python_model_disabled(tmp_path: Path) -> None:
    (tmp_path / "raw_seed.sql").write_text("SELECT 1 AS id;\n", encoding="utf-8")
    (tmp_path / "py_model.py").write_text(
        "DISABLED = True\n"
        "DEPENDS_ON = ('raw_seed',)\n"
        "def model(context):\n"
        "    raise AssertionError('should not run when disabled')\n",
        encoding="utf-8",
    )
    project = unwind.load(tmp_path)
    assert project.models["py_model"].disabled is True


def test_load_python_model_rejects_non_bool_disabled(tmp_path: Path) -> None:
    (tmp_path / "py_model.py").write_text(
        "DISABLED = 'yes'\n"
        "def model(context):\n"
        "    return None\n",
        encoding="utf-8",
    )
    with pytest.raises(ProjectLoadError, match=r"DISABLED .* must be a bool"):
        unwind.load(tmp_path)


def test_load_example_project_groups(example_models_dir: Path) -> None:
    project = unwind.load(example_models_dir)
    by_group: dict[str | None, set[str]] = {}
    for name, model in project.models.items():
        by_group.setdefault(model.group, set()).add(name)
    assert by_group["costs"] == {
        "raw_orders",
        "raw_shipments",
        "ref_carrier_rates",
        "ref_local_taxes",
        "int_order_base",
        "int_orders_dedup",
        "int_transport_costs",
        "int_tax_costs",
    }
    assert by_group["margin"] == {
        "raw_refunds",
        "int_net_margin_per_order",
        "fct_warehouse_profitability",
    }
    assert None not in by_group
