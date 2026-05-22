"""Extract column descriptions and free-form annotations from rendered SQL.

The convention is intentionally narrow (so attribution is unambiguous):

  1. A `--` comment at the very top of the file is captured by the loader as
     the model description — not handled here.
  2. A `--` comment on the **same line** as a projection expression of the
     outermost SELECT is attributed as that column's description.
  3. Every other `--` comment becomes a free-form `Annotation` with its
     line number, so it stays visible in the rendered SQL view without
     being attributed to a column it doesn't belong to.

For Python models, the description is just `module.__doc__`, captured by the
loader. There is no native column-level documentation: column descriptions
flow in by lineage inheritance (see `unwind.docs.build`).
"""

from __future__ import annotations

import re

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError

from unwind._sql import DIALECT
from unwind.docs.ir import Annotation

# Identifier (possibly quoted with " or `) at the very end of the code part
# of a line, optionally preceded by `AS`. We use this to attribute a trailing
# `--` to the right column when the line is `... col,` or `... AS col`.
_TRAILING_IDENT_RE = re.compile(
    r"""
    (?:                                  # the alias (preferred) or bare name
        \bAS\s+ ["`]?(?P<alias>\w+)["`]?
        |
        ["`]?(?P<bare>\w+)["`]?
    )
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_column_descriptions(
    rendered_sql: str,
    parsed_tree: exp.Expression | None = None,
) -> tuple[dict[str, str], tuple[Annotation, ...]]:
    """Return `({column_name: description}, free_annotations)` for a rendered SQL.

    `column_name` is the alias (or bare identifier) of an expression in the
    outermost SELECT projection. Annotations carry 1-indexed line numbers
    inside `rendered_sql`. A SQL that fails to parse returns empty results
    rather than raising — documentation should never block on a bad model.

    `parsed_tree` is an optimisation: when the caller already has a
    sqlglot AST for `rendered_sql` (e.g. shared with `build_dag`), pass it
    in to skip a second parse — meaningful on wide projects.
    """
    column_names = _outer_select_column_names(rendered_sql, parsed_tree)

    descriptions: dict[str, str] = {}
    annotations: list[Annotation] = []
    for line_no, line in enumerate(rendered_sql.splitlines(), start=1):
        idx = _find_comment_start(line)
        if idx < 0:
            continue
        code_part = line[:idx].rstrip().rstrip(",").rstrip()
        comment = line[idx + 2 :].strip()
        if not comment:
            continue
        attributed = _attribute_to_column(code_part, column_names)
        if attributed is not None and attributed not in descriptions:
            descriptions[attributed] = comment
        else:
            annotations.append(Annotation(line=line_no, text=comment))

    return descriptions, tuple(annotations)


def _outer_select_column_names(
    rendered_sql: str, parsed_tree: exp.Expression | None = None
) -> set[str]:
    """Return the alias/name set of the outermost SELECT projection.

    Returns an empty set when the SQL doesn't parse, when it isn't a SELECT,
    or when it is a `SELECT *` with no resolvable column list.
    """
    if parsed_tree is not None:
        tree = parsed_tree
    else:
        try:
            tree = parse_one(rendered_sql, dialect=DIALECT)
        except SqlglotError:
            return set()

    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is None:
        return set()

    names: set[str] = set()
    for projection in select.expressions:
        if isinstance(projection, exp.Star):
            continue
        name = projection.alias_or_name
        if name and name != "*":
            names.add(name)
    return names


def _find_comment_start(line: str) -> int:
    """Return the index of `--` outside a single-quoted string, or -1.

    Best-effort scan: handles the common case of `'…'` literals, ignores
    escape sequences (rare in DuckDB SQL) and `/* … */` blocks (we do not
    capture those — only `--` line comments are part of the convention).
    """
    in_string = False
    i = 0
    n = len(line)
    while i < n:
        char = line[i]
        if char == "'":
            in_string = not in_string
        elif (
            not in_string
            and char == "-"
            and i + 1 < n
            and line[i + 1] == "-"
        ):
            return i
        i += 1
    return -1


def _attribute_to_column(code_part: str, column_names: set[str]) -> str | None:
    """Return the projection column this code line ends on, if any."""
    if not code_part or not column_names:
        return None
    match = _TRAILING_IDENT_RE.search(code_part)
    if match is None:
        return None
    candidate = match.group("alias") or match.group("bare")
    return candidate if candidate in column_names else None
