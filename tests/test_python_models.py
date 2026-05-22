"""Tests for the Python-model feature (load, plan, run)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import duckdb
import pytest

import unwind
from unwind.errors import ProjectLoadError
from unwind.project import Model, PythonModel
from unwind.runner import RunError


def _write(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")


def test_load_registers_python_model(tmp_path: Path) -> None:
    _write(
        tmp_path / "raw_x.py",
        """
        import pyarrow as pa

        GROUP = "raw"
        TAGS = ("first",)
        MATERIALIZED = "table"

        def model(context):
            return pa.table({"x": [1, 2, 3]})
        """,
    )

    project = unwind.load(tmp_path)
    raw_x = project.models["raw_x"]
    assert isinstance(raw_x, PythonModel)
    assert raw_x.group == "raw"
    assert raw_x.tags == ("first",)
    assert raw_x.materialized == "table"
    assert raw_x.depends_on == ()


def test_python_model_runs_and_is_queryable(tmp_path: Path) -> None:
    _write(
        tmp_path / "raw_x.py",
        """
        import pyarrow as pa

        def model(context):
            return pa.table({"x": [10, 20, 30]})
        """,
    )
    _write(
        tmp_path / "fct_doubled.sql",
        "SELECT x * 2 AS xx FROM raw_x ORDER BY x;\n",
    )

    result = unwind.load(tmp_path).run()
    by_name = {m.name: m.row_count for m in result.executed}
    assert by_name == {"raw_x": 3, "fct_doubled": 3}
    # Topological invariant: raw_x runs before fct_doubled.
    assert result.names == ["raw_x", "fct_doubled"]


def test_python_model_helper_import_uses_models_dir(tmp_path: Path) -> None:
    """Sibling .py files without `model` are importable as plain helpers."""
    _write(
        tmp_path / "helpers.py",
        """
        import pyarrow as pa

        def build():
            return pa.table({"x": [7, 8]})
        """,
    )
    _write(
        tmp_path / "raw_x.py",
        """
        from helpers import build

        def model(context):
            return build()
        """,
    )

    result = unwind.load(tmp_path).run()
    by_name = {m.name: m.row_count for m in result.executed}
    assert by_name == {"raw_x": 2}


def test_python_model_view_materialization_creates_view(tmp_path: Path) -> None:
    _write(
        tmp_path / "raw_x.py",
        """
        import pyarrow as pa

        MATERIALIZED = "view"

        def model(context):
            return pa.table({"x": [1, 2]})
        """,
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
    assert kinds.get("raw_x") == "VIEW"


def test_python_model_depends_on_is_respected(tmp_path: Path) -> None:
    """A Python sink can declare DEPENDS_ON to land after its upstream SQL."""
    _write(
        tmp_path / "src.sql",
        "SELECT * FROM (VALUES (1), (2), (3)) AS t(id);\n",
    )
    _write(
        tmp_path / "sink_export.py",
        """
        DEPENDS_ON = ("src",)

        def model(context):
            # Side-effecting sink: register a one-row summary derived from src.
            context.connection.execute(
                "CREATE OR REPLACE TABLE sink_export AS "
                "SELECT COUNT(*) AS n FROM src"
            )
            return None
        """,
    )

    result = unwind.load(tmp_path).run()
    assert result.names == ["src", "sink_export"]
    by_name = {m.name: m.row_count for m in result.executed}
    assert by_name == {"src": 3, "sink_export": 1}


def test_python_model_with_unknown_depends_on_raises(tmp_path: Path) -> None:
    _write(
        tmp_path / "raw_x.py",
        """
        DEPENDS_ON = ("does_not_exist",)

        def model(context):
            import pyarrow as pa
            return pa.table({"x": []})
        """,
    )
    with pytest.raises(unwind.DAGError, match="DEPENDS_ON"):
        unwind.load(tmp_path).run()


def test_python_model_returning_sql_string(tmp_path: Path) -> None:
    _write(
        tmp_path / "raw_x.py",
        """
        def model(context):
            return "SELECT * FROM (VALUES (1), (2)) AS t(x)"
        """,
    )
    result = unwind.load(tmp_path).run()
    assert result.executed[0].row_count == 2


def test_python_model_returning_none_without_registering_fails(tmp_path: Path) -> None:
    _write(
        tmp_path / "raw_x.py",
        """
        def model(context):
            return None  # forgot to register
        """,
    )
    with pytest.raises(RunError, match="did not register"):
        unwind.load(tmp_path).run()


def test_python_model_duplicate_with_sql_name_raises(tmp_path: Path) -> None:
    _write(tmp_path / "raw_x.sql", "SELECT 1 AS a;\n")
    _write(
        tmp_path / "raw_x.py",
        """
        def model(context):
            import pyarrow as pa
            return pa.table({"a": [1]})
        """,
    )
    with pytest.raises(ProjectLoadError, match="duplicate model name 'raw_x'"):
        unwind.load(tmp_path)


def test_python_model_invalid_materialized_raises(tmp_path: Path) -> None:
    _write(
        tmp_path / "raw_x.py",
        """
        MATERIALIZED = "external"

        def model(context):
            import pyarrow as pa
            return pa.table({"a": [1]})
        """,
    )
    with pytest.raises(ProjectLoadError, match="MATERIALIZED"):
        unwind.load(tmp_path)


def test_python_model_runtime_error_wraps_in_run_error(tmp_path: Path) -> None:
    _write(
        tmp_path / "raw_x.py",
        """
        def model(context):
            raise RuntimeError("boom")
        """,
    )
    with pytest.raises(RunError, match="raw_x"):
        unwind.load(tmp_path).run()


def test_python_model_arrow_value_lineage_reaches_leaf(tmp_path: Path) -> None:
    """A Python source must surface as a clean leaf in column lineage / trace."""
    _write(
        tmp_path / "raw_orders.py",
        """
        import pyarrow as pa

        def model(context):
            return pa.table({
                "order_id": ["A", "B", "C"],
                "gross_sales": [10.0, 20.0, 30.0],
            })
        """,
    )
    _write(
        tmp_path / "fct_totals.sql",
        "SELECT order_id, gross_sales * 2 AS doubled FROM raw_orders;\n",
    )

    project = unwind.load(tmp_path)
    trace = project.trace_value(
        model="fct_totals",
        column="doubled",
        where={"order_id": "B"},
    )
    assert trace.root.values == (40.0,)
    # The leaf must report `raw_orders` as the source, not a SQL alias.
    leaf_models = _leaf_models(trace.root)
    assert "raw_orders" in leaf_models


def _leaf_models(node: object) -> set[str]:
    seen: set[str] = set()

    def walk(n) -> None:  # type: ignore[no-untyped-def]
        if not n.upstream:
            seen.add(n.model)
            return
        for child in n.upstream:
            walk(child)

    walk(node)
    return seen


def test_loader_helpers_module_is_evicted_on_root_change(tmp_path: Path) -> None:
    """A second project's `helpers.py` must shadow the first's."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _write(
        a / "helpers.py",
        "def stamp(): return 'A'\n",
    )
    _write(
        a / "raw_x.py",
        """
        from helpers import stamp

        def model(context):
            import pyarrow as pa
            return pa.table({"who": [stamp()]})
        """,
    )
    _write(
        b / "helpers.py",
        "def stamp(): return 'B'\n",
    )
    _write(
        b / "raw_x.py",
        """
        from helpers import stamp

        def model(context):
            import pyarrow as pa
            return pa.table({"who": [stamp()]})
        """,
    )

    # Load A first, then B — B's helpers must win when we run B.
    unwind.load(a)

    db = tmp_path / "out.duckdb"
    unwind.load(b).run(database=db)
    with duckdb.connect(str(db)) as conn:
        rows = conn.execute("SELECT who FROM raw_x").fetchall()
    assert rows == [("B",)]


def test_sql_models_still_work_alongside_python_models(tmp_path: Path) -> None:
    """The SQL-only path must remain unchanged."""
    _write(
        tmp_path / "raw_x.sql",
        "SELECT 42 AS answer;\n",
    )
    project = unwind.load(tmp_path)
    assert isinstance(project.models["raw_x"], Model)
    result = project.run()
    assert result.executed[0].row_count == 1
