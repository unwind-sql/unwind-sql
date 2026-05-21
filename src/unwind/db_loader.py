"""Database loader: read SQL models (and macros) from rows of a table.

The table must have at least a name column and a SQL column. Inline
`-- @group:` / `-- @tags:` / `-- @materialized:` / `-- @location:` directives
inside the SQL are parsed exactly as for filesystem-loaded models — the DB
loader does not invent a parallel metadata convention.

Macros live in the same table, distinguished by a `kind` column whose value is
`'macro'` for macros and `'model'` (or anything else / NULL) for models. Set
`kind_column=None` to load every row as a model.

SQLAlchemy is imported lazily so it stays an optional dependency
(`pip install unwind-sql[db]`).
"""

from __future__ import annotations

from unwind.errors import ProjectLoadError
from unwind.loader import _parse_metadata
from unwind.project import Model, ModelOrPython, Project

_MACRO_KIND = "macro"


def load_from_db(  # noqa: PLR0912
    connection_string: str,
    table: str,
    *,
    name_column: str = "name",
    sql_column: str = "sql",
    kind_column: str | None = None,
    where: str | None = None,
    schema: str | None = None,
) -> Project:
    """Load a project from rows of a database table.

    Args:
        connection_string: A SQLAlchemy URL (e.g. ``postgresql://user:pw@host/db``,
            ``sqlite:///path.db``, ``mysql+pymysql://...``).
        table: Name of the table holding the SQL definitions.
        name_column: Column containing the model/macro name. Defaults to ``"name"``.
        sql_column: Column containing the raw SQL text. Defaults to ``"sql"``.
        kind_column: Optional column whose value distinguishes models from macros.
            Rows where this column equals ``"macro"`` are registered as Jinja macros;
            all other rows are registered as models. When ``None``, every row is
            treated as a model.
        where: Optional raw SQL ``WHERE`` fragment used to filter rows (e.g.
            ``"project = 'retail' AND active"``). Passed through SQLAlchemy ``text()``.
        schema: Optional schema qualifier for ``table``.

    Returns:
        A `Project` populated with raw (un-rendered) models and macros.

    Raises:
        ProjectLoadError: if SQLAlchemy is missing, the table is empty, names are
            duplicated, a row has empty/null name or SQL, or metadata parsing fails.
    """
    try:
        from sqlalchemy import MetaData, Table, create_engine, select, text  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - exercised in tests with monkeypatch
        raise ProjectLoadError(
            "load_from_db requires SQLAlchemy; install with "
            "`uv pip install unwind-sql[db]` (or `pip install unwind-sql[db]`)"
        ) from exc

    engine = create_engine(connection_string)
    metadata = MetaData()
    try:
        table_obj = Table(table, metadata, autoload_with=engine, schema=schema)
    except Exception as exc:  # SQLAlchemy raises various subclasses
        raise ProjectLoadError(f"could not reflect table {table!r}: {exc}") from exc

    columns = {c.name for c in table_obj.columns}
    required = {name_column, sql_column}
    if kind_column is not None:
        required.add(kind_column)
    missing = required - columns
    if missing:
        raise ProjectLoadError(
            f"table {table!r} is missing required column(s): {sorted(missing)}"
        )

    select_cols = [table_obj.c[name_column], table_obj.c[sql_column]]
    if kind_column is not None:
        select_cols.append(table_obj.c[kind_column])

    stmt = select(*select_cols)
    if where:
        stmt = stmt.where(text(where))

    qualified = f"{schema}.{table}" if schema else table

    models: dict[str, ModelOrPython] = {}
    macros: dict[str, str] = {}

    with engine.connect() as conn:
        rows = conn.execute(stmt).all()

    for row in rows:
        name = row[0]
        raw_sql = row[1]
        kind = row[2] if kind_column is not None else None

        if not name or not isinstance(name, str):
            raise ProjectLoadError(
                f"row in {qualified!r} has empty or non-string {name_column!r}: {name!r}"
            )
        if not raw_sql or not isinstance(raw_sql, str):
            raise ProjectLoadError(
                f"row {name!r} in {qualified!r} has empty or non-string {sql_column!r}"
            )

        origin = f"db:{qualified}#{name}"

        if kind == _MACRO_KIND:
            if name in macros:
                raise ProjectLoadError(
                    f"duplicate macro name {name!r} in {qualified!r}"
                )
            macros[name] = raw_sql
            continue

        if name in models:
            raise ProjectLoadError(
                f"duplicate model name {name!r} in {qualified!r}"
            )
        group, tags, materialized, location, disabled = _parse_metadata(
            raw_sql, source=origin
        )
        models[name] = Model(
            name=name,
            path=None,
            origin=origin,
            raw_sql=raw_sql,
            group=group,
            tags=tags,
            materialized=materialized,
            location=location,
            disabled=disabled,
        )

    if not models:
        raise ProjectLoadError(
            f"no models found in {qualified!r}"
            + (f" matching where={where!r}" if where else "")
        )

    return Project(models=models, macros=macros, root=None)
