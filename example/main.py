"""End-to-end demo of the implemented features.

Run from the `example/` directory:

    uv run python generate_data.py            # one-time: write the parquet sources
    uv run --env-file ../.env python main.py  # set OPENAI_API_KEY for the LLM step

`raw_orders` is a Python model (`models/raw_orders.py`) that reads the bundled
parquet fixture via `pyarrow`. Edit `models/helpers.py` to wire it to your own
source (Postgres, Oracle, S3, ...) — Unwind ships no DB-specific dependencies.
"""

import os

import unwind


# 1. Load a project from a directory of `.sql` and `.py` files. A `.py` file
#    defining `def model(context)` is registered as a Python model in the DAG.
project = unwind.load("models/")

# 1. Alternative : charger les définitions SQL depuis des lignes que vous
#    fetchez vous-même (table de catalogue, registry YAML, endpoint HTTP, ...).
#    Unwind ne se connecte à rien — c'est vous qui fournissez les rows.
#
# rows = [
#     {"name": "stg_users", "sql": "SELECT id FROM raw_users", "kind": "model"},
#     {"name": "plus_one", "sql": "{% macro plus_one(c) %}{{c}}+1{% endmacro %}", "kind": "macro"},
# ]
# project = unwind.load_from_rows(rows, origin="catalog.sql_defs")

# 2. Exécution du DAG sur DuckDB
run_result = project.run(vars={"d_reporting": "31/10/2025"}, debug=True)
print(f"\n{len(run_result.executed)} models executed in {run_result.total_duration_s:.2f}s")

# par défaut, .run() execute tout le DAG, mais on peut aussi cibler une table spécifique
# (backtracking automatique des dépendances)
# run_result = project.run(target="int_tax_costs")


# ==========================================
# PARTIE A : Lineage Statique (Instantané)
# ==========================================

# Lineage de table (retourne un graphe statique)
table_graph = project.get_table_lineage("int_tax_costs")
print(f"\ntable lineage of 'int_tax_costs': {len(table_graph.nodes)} nodes")

# Lineage de colonne (retourne les dépendances AST)
col_graph = project.get_column_lineage("int_tax_costs", column="local_tax_fee")
print(f"column lineage of 'int_tax_costs.local_tax_fee': {col_graph.expression}")


# ==========================================
# PARTIE B : Value Lineage (Déterministe)
# ==========================================

# Traçage Déterministe : remonte la cellule cible aux valeurs sources qui ont contribué
trace_result = project.trace_value(
    model="int_tax_costs", column="local_tax_fee", where={"order_id": "ORD-7892"}
)
print("\nvalue trace of 'int_tax_costs.local_tax_fee' for ORD-7892:")
print(f"  computed value: {trace_result.root.values}")


def _show_trace(node, indent=2):
    pad = " " * indent
    print(f"{pad}{node.model}.{node.column} = {node.values}")
    print(f"{pad}  formula:     {node.expression}")
    print(f"{pad}  substituted: {node.substituted}")
    for child in node.upstream:
        _show_trace(child, indent + 2)


_show_trace(trace_result.root)


# ==========================================
# PARTIE C : Investigator LLM (pydantic-ai, multi-provider)
# ==========================================

if os.environ.get("OPENAI_API_KEY"):
    investigator = project.get_investigator(llm_provider="openai")
    explanation = investigator.explain_trace(trace_result)
    print("\n=== LLM explanation ===")
    print(explanation.summary)
    if explanation.findings:
        print("\nFindings:")
        for f in explanation.findings:
            print(f"  - {f.model}.{f.column} = {f.value}: {f.reason}")
else:
    print("\n[LLM investigator skipped — set OPENAI_API_KEY to enable]")


# ==========================================
# UI Web pour explorer le DAG (table + colonne)
# ==========================================

# Bloque jusqu'à Ctrl+C ; ouvre le navigateur sur http://127.0.0.1:8765
# (réutilise la connexion DuckDB du run — instantané, pas de recalcul).
run_result.show()
