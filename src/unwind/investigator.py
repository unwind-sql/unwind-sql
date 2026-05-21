"""LLM-driven investigator: turns a `TraceResult` into a natural-language explanation.

Backed by `pydantic-ai` for typed structured output across providers (OpenAI,
Anthropic, Google, Mistral, Groq, Ollama, …). The LLM never executes SQL or
sees the underlying tables — only the deterministic value-lineage tree
produced by `Project.trace_value(...)` — so the explanation grounds itself in
real values rather than hallucinated computations.

`Investigator` accepts either a model string (`"openai:gpt-4o-mini"`,
`"anthropic:claude-haiku-4-5"`, …) or a pre-built `pydantic_ai.models.Model`
instance (handy for tests with `TestModel`).
"""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError
from pydantic_ai.models import Model

from unwind.errors import UnwindError
from unwind.trace import TraceNode, TraceResult


class InvestigatorError(UnwindError):
    """Raised when the LLM call fails or its response cannot be parsed."""


class Finding(BaseModel):
    """One specific observation flagged by the LLM."""

    model_config = ConfigDict(frozen=True)

    model: str = Field(description="Unwind model name (e.g. 'raw_shipments').")
    column: str = Field(description="Column name within that model.")
    value: str = Field(description="The concrete value, formatted as a string.")
    reason: str = Field(description="One-sentence explanation of why it is notable.")


class Explanation(BaseModel):
    """Structured explanation produced by the LLM for a single trace."""

    model_config = ConfigDict(frozen=True)

    summary: str = Field(
        description=(
            "2 to 4 sentences in plain language describing how the target "
            "value was computed and what is notable."
        )
    )
    findings: list[Finding] = Field(
        default_factory=list,
        description="Suspicious or notable values; empty if everything looks ordinary.",
    )


_DEFAULT_MODEL_BY_PROVIDER: dict[str, str] = {
    "openai": "openai:gpt-4o-mini",
    "anthropic": "anthropic:claude-haiku-4-5",
    "google": "google-gla:gemini-2.0-flash",
    "groq": "groq:llama-3.3-70b-versatile",
    "mistral": "mistral:mistral-small-latest",
}


_SYSTEM_PROMPT = """\
You are a senior data analyst. The user gives you a deterministic value-lineage
trace of a single cell from a SQL pipeline. Each node lists, in order:
  1. the (model, column) reference and its concrete value(s),
  2. the SQL formula that computes the value (column references intact),
  3. the same formula with column references replaced by their actual values.

Your task:
- Write a concise (2 to 4 sentences) plain-language `summary` of how the target
  value was computed. Quote concrete numbers from the trace; do NOT speculate
  about values that are not in the trace.
- Populate `findings` with values that look suspicious — extreme magnitudes
  vs. their siblings, NULL where a number is expected, divisions by very small
  numbers, etc. Each finding includes (model, column, value) and a one-sentence
  reason. Return an empty list if everything looks ordinary.

Stay grounded in the data shown. Never invent column names, models, or numbers.
"""


class Investigator:
    """Wraps a `pydantic_ai.Agent` configured to return an `Explanation`."""

    def __init__(
        self,
        model: str | Model = "openai:gpt-4o-mini",
        *,
        language: str = "en",
        system_prompt: str | None = None,
    ) -> None:
        self.model = model
        self.language = language
        self.agent = Agent(
            model,
            output_type=Explanation,
            system_prompt=system_prompt or _SYSTEM_PROMPT,
        )

    def explain_trace(self, trace_result: TraceResult) -> Explanation:
        """Run the LLM on the rendered trace and return the structured explanation."""
        prompt = format_trace_prompt(trace_result, language=self.language)
        try:
            result = self.agent.run_sync(prompt)
        except UnexpectedModelBehavior as exc:
            raise InvestigatorError(f"LLM returned malformed output: {exc}") from exc
        except UserError as exc:
            raise InvestigatorError(f"LLM call failed: {exc}") from exc
        return cast("Explanation", result.output)


def get_investigator(
    *,
    llm_provider: str = "openai",
    model: str | Model | None = None,
    language: str = "en",
) -> Investigator:
    """Factory mapping a provider keyword to a default model, with override."""
    if model is None:
        if llm_provider not in _DEFAULT_MODEL_BY_PROVIDER:
            allowed = ", ".join(sorted(_DEFAULT_MODEL_BY_PROVIDER))
            raise InvestigatorError(
                f"unknown llm_provider {llm_provider!r}; allowed: {allowed}, "
                "or pass `model=...` directly."
            )
        model = _DEFAULT_MODEL_BY_PROVIDER[llm_provider]
    return Investigator(model=model, language=language)


def format_trace_prompt(trace: TraceResult, *, language: str = "en") -> str:
    """Render a `TraceResult` as the user-message text for the LLM."""
    lines = [
        f"Target cell: {trace.model}.{trace.column}",
        f"Predicate: {dict(trace.where)}",
        f"Computed value(s): {list(trace.root.values)}",
        f"Output language: {language}",
        "",
        "Value-lineage tree (each node: model.column = values, formula, substituted):",
    ]
    _render_node(trace.root, lines, indent=0)
    return "\n".join(lines)


def _render_node(node: TraceNode, lines: list[str], indent: int) -> None:
    pad = "  " * indent
    lines.append(f"{pad}{node.model}.{node.column} = {list(node.values)}")
    lines.append(f"{pad}  formula:     {node.expression}")
    lines.append(f"{pad}  substituted: {node.substituted}")
    for child in node.upstream:
        _render_node(child, lines, indent + 1)
