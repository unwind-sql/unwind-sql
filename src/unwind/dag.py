"""SQLGlot-backed dependency graph for rendered models.

Each SQL model's rendered SQL is parsed once; every `exp.Table` reference
becomes either:

- an **internal** dependency (the name matches another model in the project), or
- an **external** source (raw/ref tables registered by the runner).

CTE-local names (`WITH foo AS (...) SELECT * FROM foo`) are excluded.

Python models have no SQL to parse, so their dependencies come from the
explicit `DEPENDS_ON` constant declared in the module.

Topological order is computed with `graphlib.TopologicalSorter`; cycles surface
as `DAGError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from unwind._sql import DIALECT
from unwind.errors import UnwindError
from unwind.project import Project, PythonModel


class DAGError(UnwindError):
    """Raised when the dependency graph cannot be built (parse error, cycle, ...)."""


@dataclass(frozen=True, slots=True)
class Node:
    """A model and its resolved dependencies."""

    name: str
    depends_on_models: frozenset[str]
    depends_on_sources: frozenset[str]


@dataclass(frozen=True, slots=True)
class DAG:
    """A frozen dependency graph over a project's rendered models."""

    nodes: dict[str, Node]
    sources: frozenset[str]
    execution_order: tuple[str, ...]

    def upstream(self, model: str, *, include_self: bool = False) -> frozenset[str]:
        """Return the transitive set of model names `model` depends on."""
        self._require(model)
        seen: set[str] = set()
        stack: list[str] = [model]
        while stack:
            current = stack.pop()
            for dep in self.nodes[current].depends_on_models:
                if dep not in seen:
                    seen.add(dep)
                    stack.append(dep)
        if include_self:
            seen.add(model)
        return frozenset(seen)

    def downstream(self, model: str, *, include_self: bool = False) -> frozenset[str]:
        """Return the transitive set of model names that depend on `model`."""
        self._require(model)
        reverse: dict[str, set[str]] = {n: set() for n in self.nodes}
        for node in self.nodes.values():
            for dep in node.depends_on_models:
                reverse[dep].add(node.name)
        seen: set[str] = set()
        stack: list[str] = [model]
        while stack:
            current = stack.pop()
            for child in reverse.get(current, ()):
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        if include_self:
            seen.add(model)
        return frozenset(seen)

    def subdag(self, target: str) -> DAG:
        """Return a new DAG limited to `target` and its transitive upstream."""
        keep = self.upstream(target, include_self=True)
        kept_nodes = {n: self.nodes[n] for n in keep}
        sources = frozenset().union(*(node.depends_on_sources for node in kept_nodes.values()))
        order = tuple(n for n in self.execution_order if n in keep)
        return DAG(nodes=kept_nodes, sources=sources, execution_order=order)

    def _require(self, model: str) -> None:
        if model not in self.nodes:
            raise DAGError(f"unknown model: {model!r}")


def build_dag(
    project: Project,
    *,
    parsed_trees: dict[str, exp.Expression] | None = None,
) -> DAG:
    """Build a dependency graph from a rendered project.

    Args:
        project: A `Project` whose SQL models have been rendered
            (`Model.rendered_sql` populated). Python models are picked up
            via their `depends_on` tuple.
        parsed_trees: Optional `{model_name: sqlglot_ast}` cache. When a
            caller already parsed the rendered SQL (e.g. `build_documentation`
            sharing parses with `parse_column_descriptions`), pass it in to
            skip the parse here.

    Raises:
        DAGError: if a SQL model is unrendered, fails to parse, a Python
            model references an unknown upstream, or the graph contains
            a cycle.
    """
    model_names = frozenset(project.models)
    nodes: dict[str, Node] = {}
    all_sources: set[str] = set()

    for name, model in project.models.items():
        if isinstance(model, PythonModel):
            unknown = [d for d in model.depends_on if d not in model_names]
            if unknown:
                raise DAGError(
                    f"Python model {name!r} declares unknown DEPENDS_ON: {unknown}"
                )
            nodes[name] = Node(
                name=name,
                depends_on_models=frozenset(model.depends_on),
                depends_on_sources=frozenset(),
            )
            continue
        if model.rendered_sql is None:
            raise DAGError(f"model {name!r} is not rendered; call Project.render(...) first")
        cached_tree = parsed_trees.get(name) if parsed_trees is not None else None
        refs = _extract_refs(name, model.rendered_sql, tree=cached_tree)
        deps = refs & model_names
        sources = refs - model_names
        nodes[name] = Node(
            name=name,
            depends_on_models=frozenset(deps),
            depends_on_sources=frozenset(sources),
        )
        all_sources |= sources

    return DAG(
        nodes=nodes,
        sources=frozenset(all_sources),
        execution_order=_topological_order(nodes),
    )


def _extract_refs(
    model_name: str,
    rendered_sql: str,
    *,
    tree: exp.Expression | None = None,
) -> set[str]:
    if tree is None:
        try:
            tree = cast(
                "exp.Expression", sqlglot.parse_one(rendered_sql, dialect=DIALECT)
            )
        except ParseError as exc:
            raise DAGError(f"failed to parse model {model_name!r}: {exc}") from exc

    cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    refs: set[str] = set()
    for table in tree.find_all(exp.Table):
        name = table.name
        if not name or name in cte_names:
            continue
        refs.add(name)
    return refs


def _topological_order(nodes: dict[str, Node]) -> tuple[str, ...]:
    sorter: TopologicalSorter[str] = TopologicalSorter()
    for node in nodes.values():
        sorter.add(node.name, *node.depends_on_models)
    try:
        return tuple(sorter.static_order())
    except CycleError as exc:
        cycle = " -> ".join(exc.args[1])
        raise DAGError(f"cycle detected in model dependencies: {cycle}") from exc
