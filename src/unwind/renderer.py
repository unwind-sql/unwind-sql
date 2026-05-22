"""Jinja rendering of SQL models with shared macros.

A single `jinja2.Environment` is built per render pass. Macros in
`project.macros` (loaded by `unwind.load` from `macros/*.sql`, or by
`unwind.load_from_rows` from rows tagged as macros) are imported into the
global namespace so any model can call `{{ apply_fee(...) }}` without an
explicit `{% import %}`. User `vars` are exposed as top-level template
variables.

Undefined variables raise (`StrictUndefined`) — silent `None` substitutions are
the most common source of subtle SQL bugs.

Python models have no SQL to render, so they're carried through unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping

from jinja2 import Environment, StrictUndefined, TemplateError

from unwind.errors import TemplateRenderError
from unwind.project import Model, ModelOrPython, Project, PythonModel


def render_project(
    project: Project,
    *,
    variables: Mapping[str, object] | None = None,
) -> Project:
    """Return a new `Project` with every SQL model's `rendered_sql` populated.

    Args:
        project: A loaded project (typically from `unwind.load`).
        variables: User-provided template variables (e.g. `{"d_reporting": ...}`).
            Override the built-in `project_root` if they collide.

    Raises:
        TemplateRenderError: if any SQL model fails to render.
    """
    env = _build_environment(project.macros)
    context: dict[str, object] = {}
    if project.root is not None:
        context["project_root"] = project.root.as_posix()
    if variables:
        context.update(variables)

    rendered_models: dict[str, ModelOrPython] = {}
    for name, model in project.models.items():
        if isinstance(model, PythonModel):
            rendered_models[name] = model
            continue
        rendered_location: str | None = None
        if model.location is not None:
            try:
                rendered_location = env.from_string(model.location).render(**context)
            except TemplateError as exc:
                raise TemplateRenderError(
                    f"{model.name} (location)", str(exc)
                ) from exc
        rendered_models[name] = Model(
            name=model.name,
            path=model.path,
            origin=model.origin,
            raw_sql=model.raw_sql,
            rendered_sql=_render_one(env, model, context),
            group=model.group,
            tags=model.tags,
            materialized=model.materialized,
            location=model.location,
            rendered_location=rendered_location,
            disabled=model.disabled,
        )

    return Project(
        models=rendered_models,
        macros=project.macros,
        root=project.root,
    )


def _build_environment(macros: Mapping[str, str]) -> Environment:
    env = Environment(
        autoescape=False,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    for _macro_name, macro_sql in sorted(macros.items()):
        macro_module = env.from_string(macro_sql).module
        for attr in dir(macro_module):
            if attr.startswith("_"):
                continue
            env.globals[attr] = getattr(macro_module, attr)
    return env


def _render_one(env: Environment, model: Model, context: Mapping[str, object]) -> str:
    try:
        template = env.from_string(model.raw_sql)
        return template.render(**context)
    except TemplateError as exc:
        raise TemplateRenderError(model.name, str(exc)) from exc
