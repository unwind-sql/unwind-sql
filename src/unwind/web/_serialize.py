"""Coerce DuckDB cell values to JSON-serializable primitives."""

from __future__ import annotations

import math
from typing import Any


def jsonable(value: Any) -> Any:
    """Return a value that json.dumps can serialize.

    Native primitives pass through. NaN/Inf collapse to None. Anything else
    (date, datetime, decimal, bytes, ...) is coerced to its `str()` form.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    return str(value)
