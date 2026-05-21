"""Filesystem loader: discover `.sql` and `.py` models under a project root.

Layout convention (matches dbt/SQLMesh expectations):

    project_root/
        macros/                # any `.sql` here is treated as a Jinja macro file
            *.sql
        **/*.sql               # every other `.sql` file is a SQL model
        **/*.py                # every `.py` defining a top-level `model(context)`
                               # callable is a Python model; other `.py` files
                               # are imported as plain helper modules.

A model's name is its filename stem; collisions across subdirectories or
across `.sql` and `.py` raise `ProjectLoadError`. Files under `macros/` are
not registered as models, regardless of extension.

SQL model files may declare metadata via leading comment directives, scanned
before the first non-comment line:

    -- @group: <name>          # exclusive group (drives visual grouping)
    -- @tags: <a>, <b>, <c>    # free-form labels (filtering only)
    -- @materialized: <kind>   # 'table' (default) | 'view' | 'external'
    -- @location: <path>       # required + only valid with `external`
    -- @disabled: true|false   # Blender-style mute: skip the model body and
                               # alias its name to the first parent (default false)

Python model files declare the same metadata via module-level constants:
`GROUP`, `TAGS`, `MATERIALIZED`, `DEPENDS_ON`, `DISABLED`. `MATERIALIZED` must
be `"table"` (default) or `"view"`; `"external"` is not supported for Python
models in this version. `DEPENDS_ON` is the explicit tuple of upstream model
names (Python models have no SQL to parse). `DISABLED` is a bool (default
False); when True the function is not called and the model name is aliased to
the first parent at runtime.

The loader prepends the project root to `sys.path` so any model file can
`from helpers import ...` without ceremony.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from unwind.errors import ProjectLoadError
from unwind.project import Model, ModelOrPython, Project, PythonModel

MACROS_DIRNAME = "macros"
SQL_SUFFIX = ".sql"
PY_SUFFIX = ".py"

_DIRECTIVE_RE = re.compile(r"--\s*@(\w+)\s*:\s*(.*?)\s*$")
_MATERIALIZED_VALUES = ("table", "view", "external")
_PY_MATERIALIZED_VALUES = ("table", "view")


def load(path: str | Path) -> Project:
    """Load a project from a directory of `.sql` and `.py` files.

    Args:
        path: Path to the project root. Must be an existing directory.

    Returns:
        A `Project` populated with raw (un-rendered) models.

    Raises:
        ProjectLoadError: if the path is missing, not a directory, contains no
            models, or has duplicate model names.
    """
    root = Path(path).resolve()
    if not root.exists():
        raise ProjectLoadError(f"project path does not exist: {root}")
    if not root.is_dir():
        raise ProjectLoadError(f"project path is not a directory: {root}")

    _add_to_sys_path(root)

    macros_dir = root / MACROS_DIRNAME
    has_macros = macros_dir.is_dir()
    macros = _load_macros(macros_dir) if has_macros else {}

    models: dict[str, ModelOrPython] = {}
    _load_sql_models(root, macros_dir if has_macros else None, models)
    _load_python_models(root, macros_dir if has_macros else None, models)

    if not models:
        raise ProjectLoadError(f"no `.sql` or `.py` models found under {root}")

    return Project(models=models, macros=macros, root=root)


def _load_macros(macros_dir: Path) -> dict[str, str]:
    return {
        macro_path.stem: macro_path.read_text(encoding="utf-8")
        for macro_path in sorted(macros_dir.glob(f"*{SQL_SUFFIX}"))
    }


def _load_sql_models(
    root: Path, macros_dir: Path | None, models: dict[str, ModelOrPython]
) -> None:
    for sql_path in sorted(root.rglob(f"*{SQL_SUFFIX}")):
        if not sql_path.is_file():
            continue
        if macros_dir is not None and sql_path.is_relative_to(macros_dir):
            continue
        name = sql_path.stem
        if name in models:
            previous = models[name].path
            raise ProjectLoadError(f"duplicate model name {name!r}: {previous} and {sql_path}")
        raw_sql = sql_path.read_text(encoding="utf-8")
        group, tags, materialized, location, disabled = _parse_metadata(
            raw_sql, source=sql_path
        )
        models[name] = Model(
            name=name,
            path=sql_path,
            origin=f"file:{sql_path}",
            raw_sql=raw_sql,
            group=group,
            tags=tags,
            materialized=materialized,
            location=location,
            disabled=disabled,
        )


def _load_python_models(
    root: Path, macros_dir: Path | None, models: dict[str, ModelOrPython]
) -> None:
    for py_path in sorted(root.rglob(f"*{PY_SUFFIX}")):
        if not py_path.is_file():
            continue
        if macros_dir is not None and py_path.is_relative_to(macros_dir):
            continue
        if py_path.name == "__init__.py":
            continue
        module = _import_user_file(py_path, root)
        func = getattr(module, "model", None)
        if not callable(func):
            # Plain helper module — the import succeeded, that's all we needed.
            continue
        name = py_path.stem
        if name in models:
            previous = models[name].path
            raise ProjectLoadError(f"duplicate model name {name!r}: {previous} and {py_path}")
        group, tags, materialized, depends_on, disabled = _parse_python_metadata(
            module, source=py_path
        )
        models[name] = PythonModel(
            name=name,
            func=func,
            path=py_path,
            origin=f"file:{py_path}",
            depends_on=depends_on,
            group=group,
            tags=tags,
            materialized=materialized,
            disabled=disabled,
        )


_LAST_ROOT: Path | None = None


def _add_to_sys_path(root: Path) -> None:
    """Insert `root` at the head of `sys.path` and evict stale modules.

    Lets every Python model in the project `from helpers import ...` without
    the user having to configure `PYTHONPATH`.

    When a different project root was loaded earlier in the same process,
    any module whose `__file__` lives under that old root is dropped from
    `sys.modules` and the old root is taken off `sys.path` — otherwise a
    second project's `from helpers import …` would resolve to the first
    project's cached `helpers` module.
    """
    global _LAST_ROOT  # noqa: PLW0603 — single source of truth for the active project root
    root_str = str(root)
    if _LAST_ROOT is not None and root != _LAST_ROOT:
        last_prefix = str(_LAST_ROOT) + ("" if str(_LAST_ROOT).endswith("/") else "/")
        stale = [
            name
            for name, module in list(sys.modules.items())
            if (file := getattr(module, "__file__", None))
            and isinstance(file, str)
            and file.startswith(last_prefix)
        ]
        for name in stale:
            sys.modules.pop(name, None)
        last_str = str(_LAST_ROOT)
        while last_str in sys.path:
            sys.path.remove(last_str)
    _LAST_ROOT = root
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


def _import_user_file(py_path: Path, root: Path) -> ModuleType:
    """Import a user-authored `.py` file under the project root.

    Uses a unique module name derived from the path-relative-to-root so that
    distinct files don't collide in `sys.modules`, and so that re-loading the
    same project picks up edits without process restart.
    """
    rel = py_path.relative_to(root).with_suffix("")
    mod_name = "unwind_user_models." + ".".join(rel.parts)
    spec = importlib.util.spec_from_file_location(mod_name, py_path)
    if spec is None or spec.loader is None:
        raise ProjectLoadError(f"could not load Python model file: {py_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        # Don't keep a half-initialised module around in sys.modules.
        sys.modules.pop(mod_name, None)
        raise ProjectLoadError(
            f"failed to import Python model file {py_path}: {exc}"
        ) from exc
    return module


def _parse_python_metadata(
    module: ModuleType, *, source: Path
) -> tuple[str | None, tuple[str, ...], str, tuple[str, ...], bool]:
    """Read module-level directive constants from a Python model.

    Recognised constants: `GROUP`, `TAGS`, `MATERIALIZED`, `DEPENDS_ON`, `DISABLED`.
    """
    group = _read_str_or_none(module, "GROUP", source=source)
    tags = _read_str_tuple(module, "TAGS", source=source)
    materialized = _read_materialized(module, source=source)
    depends_on = _read_str_tuple(module, "DEPENDS_ON", source=source)
    disabled = _read_bool(module, "DISABLED", source=source)
    return group, tags, materialized, depends_on, disabled


def _read_str_or_none(module: ModuleType, attr: str, *, source: Path) -> str | None:
    value = getattr(module, attr, None)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ProjectLoadError(
            f"{attr} in {source} must be a non-empty string, got {value!r}"
        )
    return value


def _read_str_tuple(module: ModuleType, attr: str, *, source: Path) -> tuple[str, ...]:
    value = getattr(module, attr, ())
    if isinstance(value, str) or not _is_iterable(value):
        raise ProjectLoadError(
            f"{attr} in {source} must be a tuple/list of strings, got {value!r}"
        )
    items = tuple(value)
    for item in items:
        if not isinstance(item, str) or not item:
            raise ProjectLoadError(
                f"{attr} in {source} must contain non-empty strings, got {item!r}"
            )
    return items


def _read_bool(module: ModuleType, attr: str, *, source: Path) -> bool:
    value = getattr(module, attr, False)
    if not isinstance(value, bool):
        raise ProjectLoadError(
            f"{attr} in {source} must be a bool, got {value!r}"
        )
    return value


def _read_materialized(module: ModuleType, *, source: Path) -> str:
    value = getattr(module, "MATERIALIZED", "table")
    if not isinstance(value, str) or value not in _PY_MATERIALIZED_VALUES:
        raise ProjectLoadError(
            f"MATERIALIZED in {source} must be one of {_PY_MATERIALIZED_VALUES}, got {value!r}"
        )
    return value


def _is_iterable(value: Any) -> bool:
    try:
        iter(value)
    except TypeError:
        return False
    return True


def _parse_metadata(  # noqa: PLR0912
    raw_sql: str, *, source: str | Path
) -> tuple[str | None, tuple[str, ...], str, str | None, bool]:
    """Read leading `-- @key: value` directives.

    Recognised: `@group`, `@tags`, `@materialized`, `@location`, `@disabled`.
    Plain comments and unknown directives are silently skipped. Returns
    `(group, tags, materialized, location, disabled)`.
    """
    group: str | None = None
    tags: tuple[str, ...] = ()
    materialized: str | None = None
    location: str | None = None
    disabled: bool | None = None
    for line in raw_sql.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("--"):
            break  # first non-comment line ends the header
        match = _DIRECTIVE_RE.match(stripped)
        if match is None:
            continue  # plain comment, keep scanning
        key, value = match.group(1), match.group(2).strip()
        if key == "group":
            if group is not None:
                raise ProjectLoadError(f"duplicate '@group' directive in {source}")
            if not value:
                raise ProjectLoadError(f"empty '@group' value in {source}")
            group = value
        elif key == "tags":
            if tags:
                raise ProjectLoadError(f"duplicate '@tags' directive in {source}")
            tags = tuple(t.strip() for t in value.split(",") if t.strip())
        elif key == "materialized":
            if materialized is not None:
                raise ProjectLoadError(f"duplicate '@materialized' directive in {source}")
            if value not in _MATERIALIZED_VALUES:
                raise ProjectLoadError(
                    f"invalid '@materialized' value {value!r} in {source}; "
                    f"must be one of: {', '.join(_MATERIALIZED_VALUES)}"
                )
            materialized = value
        elif key == "location":
            if location is not None:
                raise ProjectLoadError(f"duplicate '@location' directive in {source}")
            if not value:
                raise ProjectLoadError(f"empty '@location' value in {source}")
            location = value
        elif key == "disabled":
            if disabled is not None:
                raise ProjectLoadError(f"duplicate '@disabled' directive in {source}")
            lowered = value.lower()
            if lowered in ("true", "1", "yes"):
                disabled = True
            elif lowered in ("false", "0", "no", ""):
                disabled = False
            else:
                raise ProjectLoadError(
                    f"invalid '@disabled' value {value!r} in {source}; "
                    f"must be 'true' or 'false'"
                )

    if materialized == "external" and location is None:
        raise ProjectLoadError(
            f"'@materialized: external' requires '@location' in {source}"
        )
    if location is not None and materialized != "external":
        raise ProjectLoadError(
            f"'@location' is only valid with '@materialized: external' in {source}"
        )

    return group, tags, materialized or "table", location, bool(disabled)
