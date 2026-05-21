"""Microbenchmark target: nothing but `project.run(...)`.

Mirrors the line being benchmarked from main.py:
    project.run(engine="duckdb", vars={"d_reporting": "31/10/2025"}, debug=True)
"""

from __future__ import annotations

import unwind

if __name__ == "__main__":
    project = unwind.load("sql/")
    project.run(engine="duckdb", vars={"d_reporting": "31/10/2025"}, debug=True)
