"""Tests for the LLM investigator (mocked — no live API calls)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.exceptions import UserError
from pydantic_ai.models.test import TestModel

import unwind
from unwind.investigator import (
    Explanation,
    Finding,
    Investigator,
    InvestigatorError,
    format_trace_prompt,
    get_investigator,
)


@pytest.fixture
def trace_result(example_data_ready: Path):
    return unwind.load(example_data_ready).trace_value(
        model="int_tax_costs",
        column="local_tax_fee",
        where={"order_id": "ORD-7892"},
    )


# ── Prompt formatting ───────────────────────────────────────────────────────


def test_format_trace_prompt_includes_target_and_lineage(trace_result) -> None:
    prompt = format_trace_prompt(trace_result)
    assert "int_tax_costs.local_tax_fee" in prompt
    assert "ORD-7892" in prompt
    assert "raw_orders.gross_sales" in prompt  # leaf is reached
    assert "formula:" in prompt
    assert "substituted:" in prompt
    # Concrete values appear as Python list reprs
    assert "[500.0]" in prompt or "500.0" in prompt
    assert "[102.5]" in prompt or "102.5" in prompt


def test_format_trace_prompt_respects_language(trace_result) -> None:
    prompt = format_trace_prompt(trace_result, language="fr")
    assert "Output language: fr" in prompt


# ── Investigator with TestModel ─────────────────────────────────────────────


def test_explain_trace_with_test_model(trace_result) -> None:
    """`TestModel` auto-generates a valid `Explanation`; no API key required."""
    investigator = Investigator(model=TestModel())
    explanation = investigator.explain_trace(trace_result)

    assert isinstance(explanation, Explanation)
    assert isinstance(explanation.summary, str)
    assert isinstance(explanation.findings, list)
    for finding in explanation.findings:
        assert isinstance(finding, Finding)


def test_explain_trace_with_canned_output(trace_result) -> None:
    """Inject a specific output via `TestModel(custom_output_args=...)`."""
    canned = {
        "summary": "The tax fee 102.5 is gross_sales 500 * tax_pct 0.2 + fee 2.5.",
        "findings": [
            {
                "model": "raw_shipments",
                "column": "weight_kg",
                "value": "1500.0",
                "reason": "Weight outlier: 1500kg vs typical ~1.5kg.",
            }
        ],
    }
    investigator = Investigator(model=TestModel(custom_output_args=canned))
    explanation = investigator.explain_trace(trace_result)

    assert "tax fee 102.5" in explanation.summary
    assert len(explanation.findings) == 1
    assert explanation.findings[0].column == "weight_kg"
    assert explanation.findings[0].value == "1500.0"


# ── Factory and Project method ──────────────────────────────────────────────


def test_get_investigator_default_provider() -> None:
    inv = get_investigator(model=TestModel())
    assert isinstance(inv, Investigator)


def test_get_investigator_unknown_provider_raises() -> None:
    with pytest.raises(InvestigatorError, match="unknown llm_provider"):
        get_investigator(llm_provider="not-a-provider")


def test_project_get_investigator(example_data_ready: Path) -> None:
    project = unwind.load(example_data_ready)
    inv = project.get_investigator(model=TestModel())
    assert isinstance(inv, Investigator)


def test_unwind_lazy_attrs() -> None:
    """`unwind.Investigator` resolves via the package's `__getattr__`."""
    assert unwind.Investigator is Investigator
    assert unwind.Explanation is Explanation
    assert unwind.Finding is Finding
    with pytest.raises(AttributeError):
        unwind.does_not_exist  # noqa: B018


# ── Errors ──────────────────────────────────────────────────────────────────


def test_explain_trace_wraps_user_error(trace_result, monkeypatch) -> None:
    """A pydantic-ai `UserError` (e.g., bad config) must surface as `InvestigatorError`."""
    investigator = Investigator(model=TestModel())

    def boom(*_args, **_kwargs):
        raise UserError("simulated provider misconfiguration")

    monkeypatch.setattr(investigator.agent, "run_sync", boom)

    with pytest.raises(InvestigatorError, match="LLM call failed"):
        investigator.explain_trace(trace_result)
