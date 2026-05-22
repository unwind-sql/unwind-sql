"""In-process A/B comparison: all-tables vs current materialization mix.

Bypasses Python startup, measures `RunResult.total_duration_s` only.
"""

from __future__ import annotations

import statistics

from dataclasses import replace

import unwind
from unwind.project import Model, PythonModel


def _override_materialization(project: unwind.Project, kind: str) -> None:
    # Force `kind` on SQL models; Python models only support "table"/"view",
    # so coerce anything else to "table" for them.
    py_kind = kind if kind in ("table", "view") else "table"
    project.models = {
        name: replace(m, materialized=py_kind if isinstance(m, PythonModel) else kind)
        for name, m in project.models.items()
        if isinstance(m, (Model, PythonModel))
    }


def _measure(force_kind: str | None, n: int) -> list[float]:
    durations: list[float] = []
    for _ in range(n):
        project = unwind.load("models/")
        if force_kind is not None:
            _override_materialization(project, force_kind)
        result = project.run(vars={"d_reporting": "31/10/2025"})
        durations.append(result.total_duration_s)
    return durations


def _stats(label: str, values: list[float]) -> None:
    ms = [v * 1000 for v in values]
    print(
        f"{label:<28} median={statistics.median(ms):6.1f} ms  "
        f"mean={statistics.mean(ms):6.1f} ms  "
        f"stdev={statistics.pstdev(ms):5.1f} ms  "
        f"min={min(ms):6.1f} ms  max={max(ms):6.1f} ms"
    )


if __name__ == "__main__":
    runs = 20
    # Warmup parquet readers / Python imports
    _measure(force_kind="table", n=2)

    baseline = _measure(force_kind="table", n=runs)
    optimized = _measure(force_kind=None, n=runs)  # respect SQL directives

    print(f"\nIn-process pipeline duration (n={runs} per config):\n")
    _stats("baseline (all tables)", baseline)
    _stats("optimized (current mix)", optimized)

    delta_ms = (statistics.median(baseline) - statistics.median(optimized)) * 1000
    speedup = statistics.median(baseline) / statistics.median(optimized)
    print(f"\nMedian delta: -{delta_ms:.1f} ms ({speedup:.2f}x speedup)")
