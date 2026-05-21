"""Python source model for `raw_orders`.

The body is intentionally trivial: all the conditional logic for picking a
backend lives in `helpers.load_data`, so adding a new source (Postgres, S3,
REST API, …) is a one-place change that all `raw_*` models inherit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from helpers import load_data

if TYPE_CHECKING:
    from unwind import ModelContext

GROUP = "costs"
MATERIALIZED = "view"  # zero-copy registration when the backend is Arrow-native


def model(context: ModelContext):  # noqa: ARG001
    return load_data("raw_orders")
