"""Load a project from in-memory rows.

Use this when the model definitions live somewhere other than a filesystem —
a metadata table, a YAML/JSON registry, an HTTP endpoint, anywhere. Fetch
the rows yourself (Unwind has no opinion on the source) and hand them to
`load_from_rows`.

Each row carries a model `name`, its raw `sql`, and an optional `kind` —
rows whose `kind` is the string `"macro"` are registered as Jinja macros
instead of models. Inline `-- @group:` / `-- @tags:` / `-- @materialized:` /
`-- @location:` / `-- @disabled:` directives inside the SQL are honoured
exactly as for filesystem-loaded models.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, cast

from unwind.errors import ProjectLoadError
from unwind.loader import _parse_metadata
from unwind.project import Model, ModelOrPython, Project

_MACRO_KIND = "macro"

Row = Mapping[str, Any] | Sequence[Any]


def load_from_rows(
    rows: Iterable[Row],
    *,
    name_key: str = "name",
    sql_key: str = "sql",
    kind_key: str | None = "kind",
    origin: str = "rows",
) -> Project:
    """Build a `Project` from rows of `(name, sql, kind?)`.

    Args:
        rows: Iterable of dicts or tuples. Dicts are looked up via the
            `*_key` parameters; tuples are read positionally as
            `(name, sql, kind)` — `kind` is optional.
        name_key: Mapping key for the model/macro name. Ignored for tuples.
        sql_key: Mapping key for the raw SQL text. Ignored for tuples.
        kind_key: Mapping key whose value distinguishes models from macros
            (the string ``"macro"`` flags a macro; anything else is a model).
            Pass ``None`` to treat every row as a model.
        origin: Human-readable prefix used in error messages and stored on
            each `Model.origin` (e.g. ``"rows"`` → ``"rows#stg_users"``).

    Raises:
        ProjectLoadError: empty input, duplicate names, missing/blank
            ``name``/``sql`` fields, or metadata parsing errors.
    """
    models: dict[str, ModelOrPython] = {}
    macros: dict[str, str] = {}
    seen_any = False

    for index, row in enumerate(rows):
        seen_any = True
        name, raw_sql, kind = _unpack(row, name_key, sql_key, kind_key, origin, index)

        row_origin = f"{origin}#{name}"

        if kind == _MACRO_KIND:
            if name in macros:
                raise ProjectLoadError(f"duplicate macro name {name!r} in {origin}")
            macros[name] = raw_sql
            continue

        if name in models:
            raise ProjectLoadError(f"duplicate model name {name!r} in {origin}")
        group, tags, materialized, location, disabled, description = _parse_metadata(
            raw_sql, source=row_origin
        )
        models[name] = Model(
            name=name,
            path=None,
            origin=row_origin,
            raw_sql=raw_sql,
            group=group,
            tags=tags,
            materialized=materialized,
            location=location,
            disabled=disabled,
            description=description,
        )

    if not seen_any:
        raise ProjectLoadError(f"no rows provided to load_from_rows ({origin})")
    if not models:
        raise ProjectLoadError(f"no models found in {origin} (only macros)")

    return Project(models=models, macros=macros, root=None)


def _unpack(
    row: Row,
    name_key: str,
    sql_key: str,
    kind_key: str | None,
    origin: str,
    index: int,
) -> tuple[str, str, Any]:
    if isinstance(row, Mapping):
        name, raw_sql, kind = _unpack_mapping(
            cast(Mapping[str, Any], row), name_key, sql_key, kind_key, origin, index
        )
    else:
        name, raw_sql, kind = _unpack_sequence(
            cast(Sequence[Any], row), kind_key, origin, index
        )

    if not isinstance(name, str) or not name:
        raise ProjectLoadError(
            f"row {index} in {origin} has empty or non-string name: {name!r}"
        )
    if not isinstance(raw_sql, str) or not raw_sql:
        raise ProjectLoadError(
            f"row {name!r} in {origin} has empty or non-string sql"
        )
    return name, raw_sql, kind


def _unpack_mapping(
    row: Mapping[str, Any],
    name_key: str,
    sql_key: str,
    kind_key: str | None,
    origin: str,
    index: int,
) -> tuple[Any, Any, Any]:
    if name_key not in row:
        raise ProjectLoadError(f"row {index} in {origin} missing {name_key!r} field")
    if sql_key not in row:
        raise ProjectLoadError(f"row {index} in {origin} missing {sql_key!r} field")
    kind = row[kind_key] if kind_key is not None and kind_key in row else None
    return row[name_key], row[sql_key], kind


def _unpack_sequence(
    row: Sequence[Any],
    kind_key: str | None,
    origin: str,
    index: int,
) -> tuple[Any, Any, Any]:
    if len(row) < 2:
        raise ProjectLoadError(
            f"row {index} in {origin} has fewer than 2 fields (need name, sql)"
        )
    kind = row[2] if len(row) >= 3 and kind_key is not None else None
    return row[0], row[1], kind
