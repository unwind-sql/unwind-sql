"""Tests for the FastAPI web UI."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

import unwind
from unwind.investigator import Investigator
from unwind.web import build_app


@pytest.fixture
def client(example_data_ready: Path) -> Iterator[TestClient]:
    project = unwind.load(example_data_ready)
    app = build_app(project)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def llm_client(example_data_ready: Path) -> Iterator[TestClient]:
    """Client wired with a deterministic TestModel (no LLM API calls)."""
    project = unwind.load(example_data_ready)
    canned = {
        "summary": "Test summary: local_tax_fee equals 102.5 from gross_sales 500 * 0.2 + 2.5.",
        "findings": [
            {
                "model": "raw_shipments",
                "column": "weight_kg",
                "value": "1500.0",
                "reason": "Outlier weight far exceeds typical orders.",
            }
        ],
    }
    investigator = Investigator(model=TestModel(custom_output_args=canned))
    app = build_app(project, investigator=investigator)
    with TestClient(app) as c:
        yield c


def test_index_html_served(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "<title>unwind" in r.text
    # Vite-built SPA mounts a #root and pulls hashed assets from /assets/.
    assert '<div id="root"></div>' in r.text
    assert "/assets/index-" in r.text
    assert r.headers.get("Content-Type", "").startswith("text/html")
    assert r.headers.get("Cache-Control") == "no-store, must-revalidate"


def test_index_html_no_cache_header(client: TestClient) -> None:
    r = client.get("/index.html")
    assert r.status_code == 200
    assert r.headers.get("Cache-Control") == "no-store, must-revalidate"


def test_static_bundle_served(client: TestClient) -> None:
    """The Vite bundle exposes its hashed JS + CSS under /assets/."""
    index = client.get("/").text
    js_match = re.search(r'src="(/assets/index-[^"]+\.js)"', index)
    css_match = re.search(r'href="(/assets/index-[^"]+\.css)"', index)
    assert js_match is not None
    assert css_match is not None
    js = client.get(js_match.group(1))
    css = client.get(css_match.group(1))
    assert js.status_code == 200
    assert css.status_code == 200


def test_dag_endpoint_lists_nodes_and_edges(client: TestClient) -> None:
    payload = client.get("/api/dag").json()

    node_ids = {n["id"] for n in payload["nodes"]}
    assert {"raw_orders", "int_tax_costs", "fct_warehouse_profitability"} <= node_ids

    kinds = {n["id"]: n["kind"] for n in payload["nodes"]}
    assert kinds["raw_orders"] == "raw"
    assert kinds["ref_carrier_rates"] == "ref"
    assert kinds["int_tax_costs"] == "int"
    assert kinds["fct_warehouse_profitability"] == "fct"

    edges = {(e["from"], e["to"]) for e in payload["edges"]}
    assert ("int_net_margin_per_order", "fct_warehouse_profitability") in edges
    assert ("raw_orders", "int_order_base") in edges


def test_dag_endpoint_exposes_groups(client: TestClient) -> None:
    payload = client.get("/api/dag").json()

    by_id = {n["id"]: n for n in payload["nodes"]}
    assert by_id["raw_orders"]["group"] == "costs"
    assert by_id["int_tax_costs"]["group"] == "costs"
    assert by_id["raw_refunds"]["group"] == "margin"
    assert by_id["fct_warehouse_profitability"]["group"] == "margin"
    assert all("tags" in n for n in payload["nodes"])

    groups = {g["id"]: set(g["members"]) for g in payload["groups"]}
    assert set(groups) == {"costs", "margin"}
    assert "raw_orders" in groups["costs"]
    assert "fct_warehouse_profitability" in groups["margin"]
    assert len(groups["costs"]) == 7
    assert len(groups["margin"]) == 3


def test_model_endpoint_returns_columns_sql_and_neighbours(client: TestClient) -> None:
    payload = client.get("/api/model/int_tax_costs").json()

    assert payload["name"] == "int_tax_costs"
    assert payload["language"] == "sql"
    assert "ROUND(" in payload["source"]  # macro expanded
    assert payload["row_count"] == 10  # qty > 0 filter, ORD-1009 excluded

    col_names = {c["name"] for c in payload["columns"]}
    assert "local_tax_fee" in col_names
    assert "gross_sales" in col_names

    assert "int_transport_costs" in payload["upstream"]
    assert "int_net_margin_per_order" in payload["downstream"]


def test_model_endpoint_returns_python_source_for_python_models(
    client: TestClient,
) -> None:
    payload = client.get("/api/model/raw_orders").json()

    assert payload["name"] == "raw_orders"
    assert payload["language"] == "python"
    # The full `.py` file is returned: the helper import and the `model(...)`
    # body must both be readable from the UI.
    assert "def model(" in payload["source"]
    assert "load_data" in payload["source"]


def test_dag_endpoint_tags_each_node_with_its_source_language(
    client: TestClient,
) -> None:
    payload = client.get("/api/dag").json()
    by_id = {n["id"]: n for n in payload["nodes"]}

    assert by_id["raw_orders"]["language"] == "python"
    assert by_id["int_tax_costs"]["language"] == "sql"
    assert by_id["fct_warehouse_profitability"]["language"] == "sql"


def test_impact_endpoint_lists_downstream_affected_columns(
    client: TestClient,
) -> None:
    payload = client.get("/api/column/raw_orders/gross_sales/impact").json()

    assert payload["source"]["model"] == "raw_orders"
    assert payload["source"]["column"] == "gross_sales"
    assert payload["source"]["type"]  # type is reported (DuckDB names vary)

    affected = {(c["model"], c["column"]) for c in payload["affected"]}
    assert ("int_order_base", "gross_sales") in affected
    assert ("fct_warehouse_profitability", "total_revenue") in affected

    # JOIN-key usages must be detectable too.
    wh_payload = client.get("/api/column/raw_orders/warehouse_id/impact").json()
    usages = {(e["child_model"], e["usage"]) for e in wh_payload["edges"]}
    assert ("int_order_base", "join") in usages


def test_model_endpoint_unknown_model_404(client: TestClient) -> None:
    r = client.get("/api/model/does_not_exist")
    assert r.status_code == 404
    assert "error" in r.json()


def test_data_endpoint_returns_columns_and_rows(client: TestClient) -> None:
    payload = client.get("/api/model/raw_orders/data?limit=5&offset=0").json()
    assert payload["limit"] == 5
    assert payload["offset"] == 0
    assert payload["total"] >= len(payload["rows"])
    assert payload["total"] >= 1
    assert len(payload["rows"]) <= 5

    col_names = [c["name"] for c in payload["columns"]]
    assert "order_id" in col_names
    # rows are arrays in column order
    for row in payload["rows"]:
        assert isinstance(row, list)
        assert len(row) == len(col_names)


def test_data_endpoint_pagination_returns_distinct_rows(client: TestClient) -> None:
    p0 = client.get("/api/model/raw_orders/data?limit=2&offset=0").json()
    p1 = client.get("/api/model/raw_orders/data?limit=2&offset=2").json()
    if p0["total"] >= 4:
        assert p0["rows"] != p1["rows"]
    assert p0["limit"] == p1["limit"] == 2
    assert p0["offset"] == 0
    assert p1["offset"] == 2


def test_data_endpoint_unknown_model_404(client: TestClient) -> None:
    r = client.get("/api/model/does_not_exist/data")
    assert r.status_code == 404
    assert "error" in r.json()


def test_data_endpoint_validates_limit(client: TestClient) -> None:
    assert client.get("/api/model/raw_orders/data?limit=0").status_code == 422
    assert client.get("/api/model/raw_orders/data?limit=10000").status_code == 422
    assert client.get("/api/model/raw_orders/data?offset=-1").status_code == 422


def test_column_endpoint_returns_lineage_tree(client: TestClient) -> None:
    payload = client.get("/api/column/int_tax_costs/local_tax_fee").json()

    assert "LOCAL_TAX_FEE" in payload["name"].upper()
    assert "ROUND" in payload["expression"].upper()

    flat = _flatten(payload)
    assert any("GROSS_SALES" in s.upper() for s in flat)
    assert any("TAX_PCT" in s.upper() for s in flat)


def test_unknown_path_404(client: TestClient) -> None:
    assert client.get("/nope").status_code == 404


def test_cell_endpoint_returns_value_lineage(client: TestClient) -> None:
    data = client.get("/api/model/int_tax_costs/data?limit=1").json()
    cols = [c["name"] for c in data["columns"]]
    row = data["rows"][0]
    where = dict(zip(cols, row, strict=True))

    r = client.post(
        "/api/cell",
        json={"model": "int_tax_costs", "column": "local_tax_fee", "where": where},
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["model"] == "int_tax_costs"
    assert payload["column"] == "local_tax_fee"

    root = payload["root"]
    assert root["column"].lower() == "local_tax_fee"
    assert "ROUND" in root["expression"].upper()
    assert "ROUND" in root["substituted"].upper()
    assert root["values"], "root should resolve to a value"
    assert isinstance(root["upstream"], list)
    assert root["upstream"], "root should have upstream nodes"

    # walk down — at least one descendant should reach a raw_/ref_ source
    flat = _flatten_trace(root)
    assert any(node["model"].startswith(("raw_", "ref_")) for node in flat)


def test_cell_endpoint_unknown_model_404(client: TestClient) -> None:
    r = client.post(
        "/api/cell",
        json={"model": "does_not_exist", "column": "x", "where": {"k": 1}},
    )
    assert r.status_code == 404
    assert "error" in r.json()


def test_cell_endpoint_empty_where_404(client: TestClient) -> None:
    r = client.post(
        "/api/cell",
        json={"model": "int_tax_costs", "column": "local_tax_fee", "where": {}},
    )
    assert r.status_code == 404
    assert "error" in r.json()


def test_cell_endpoint_predicate_column_not_in_target(client: TestClient) -> None:
    r = client.post(
        "/api/cell",
        json={
            "model": "int_tax_costs",
            "column": "local_tax_fee",
            "where": {"nonexistent_column": 1},
        },
    )
    assert r.status_code == 404
    assert "error" in r.json()


def _flatten_trace(node: dict[str, Any]) -> list[dict[str, Any]]:
    out = [node]
    for child in node.get("upstream", []):
        out.extend(_flatten_trace(child))
    return out


def _consume_sse(client: TestClient, body: dict[str, Any]) -> list[dict[str, Any]]:
    r = client.post("/api/investigate", json=body)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    return [
        _parse_sse_block(block)
        for block in r.text.split("\n\n")
        if block.strip()
    ]


def _parse_sse_block(block: str) -> dict[str, Any]:
    event = "message"
    data = ""
    for line in block.split("\n"):
        if line.startswith("event: "):
            event = line[len("event: ") :].strip()
        elif line.startswith("data: "):
            data += line[len("data: ") :]
    return {"event": event, "data": json.loads(data) if data else None}


def test_investigate_endpoint_streams_status_and_done(llm_client: TestClient) -> None:
    data = llm_client.get("/api/model/int_tax_costs/data?limit=1").json()
    cols = [c["name"] for c in data["columns"]]
    where = dict(zip(cols, data["rows"][0], strict=True))

    events = _consume_sse(
        llm_client,
        {"model": "int_tax_costs", "column": "local_tax_fee", "where": where},
    )

    phases = [e["data"]["phase"] for e in events if e["event"] == "status"]
    assert "tracing" in phases
    assert "llm" in phases

    done = [e for e in events if e["event"] == "done"]
    assert len(done) == 1
    payload = done[0]["data"]
    assert "tax fee" in payload["summary"].lower() or payload["summary"]
    assert isinstance(payload["findings"], list)
    assert len(payload["findings"]) == 1
    assert payload["findings"][0]["column"] == "weight_kg"


def test_investigate_endpoint_caches_repeated_calls(llm_client: TestClient) -> None:
    data = llm_client.get("/api/model/int_tax_costs/data?limit=1").json()
    cols = [c["name"] for c in data["columns"]]
    where = dict(zip(cols, data["rows"][0], strict=True))
    body = {"model": "int_tax_costs", "column": "local_tax_fee", "where": where}

    first = _consume_sse(llm_client, body)
    second = _consume_sse(llm_client, body)

    first_phases = [e["data"]["phase"] for e in first if e["event"] == "status"]
    second_phases = [e["data"]["phase"] for e in second if e["event"] == "status"]
    assert "llm" in first_phases
    assert "cached" in second_phases
    assert "llm" not in second_phases

    first_done = next(e["data"] for e in first if e["event"] == "done")
    second_done = next(e["data"] for e in second if e["event"] == "done")
    assert first_done == second_done


def test_investigate_endpoint_unknown_model_emits_error(llm_client: TestClient) -> None:
    events = _consume_sse(
        llm_client,
        {"model": "does_not_exist", "column": "x", "where": {"k": 1}},
    )
    errors = [e for e in events if e["event"] == "error"]
    assert errors, "should emit an SSE error event"
    assert "error" in errors[0]["data"]
    assert not [e for e in events if e["event"] == "done"]


def _flatten(node: dict[str, Any]) -> list[str]:
    out = [node["name"]]
    for child in node.get("upstream", []):
        out.extend(_flatten(child))
    return out
