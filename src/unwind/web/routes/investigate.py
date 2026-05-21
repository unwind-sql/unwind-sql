"""POST /api/investigate — SSE-streamed natural-language explanation of a cell.

The response is `text/event-stream` with the following events:

    event: status     {"phase": "tracing" | "cached" | "llm"}
    event: done       {"summary": "...", "findings": [{model,column,value,reason}]}
    event: error      {"error": "..."}

Explanations are cached LRU on `(model, column, frozenset(where.items()))` so a
repeat click on the same cell skips the LLM round-trip entirely.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from unwind.errors import UnwindError
from unwind.trace import trace_value
from unwind.web.state import AppState, StateDep

if TYPE_CHECKING:
    # `pydantic_ai` is an optional extra — keep these behind TYPE_CHECKING.
    from unwind.investigator import Explanation, Investigator

router = APIRouter()

CACHE_MAX = 64


class InvestigateRequest(BaseModel):
    model: str
    column: str
    where: dict[str, Any] = Field(default_factory=dict)
    max_values: int | None = 5


@router.post("/api/investigate")
async def investigate(req: InvestigateRequest, state: StateDep) -> StreamingResponse:
    return StreamingResponse(
        _generate(req, state),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _generate(req: InvestigateRequest, state: AppState) -> AsyncIterator[str]:
    try:
        yield _sse("status", {"phase": "tracing"})
        trace = await asyncio.to_thread(
            trace_value,
            state.project,
            model=req.model,
            column=req.column,
            where=req.where,
            max_values=req.max_values,
        )

        cache_key = _make_cache_key(req)
        cached = state.explanation_cache.get(cache_key)
        if cached is not None:
            state.explanation_cache.move_to_end(cache_key)
            yield _sse("status", {"phase": "cached"})
            yield _sse("done", _explanation_payload(cached))
            return

        investigator = _ensure_investigator(state)
        yield _sse("status", {"phase": "llm"})
        explanation = await asyncio.to_thread(investigator.explain_trace, trace)

        state.explanation_cache[cache_key] = explanation
        while len(state.explanation_cache) > CACHE_MAX:
            state.explanation_cache.popitem(last=False)
        yield _sse("done", _explanation_payload(explanation))

    except UnwindError as exc:
        yield _sse("error", {"error": str(exc)})
    except Exception as exc:
        yield _sse("error", {"error": f"{type(exc).__name__}: {exc}"})


def _make_cache_key(req: InvestigateRequest) -> tuple[str, str, frozenset[tuple[str, Any]]]:
    return (req.model, req.column, frozenset(req.where.items()))


def _ensure_investigator(state: AppState) -> Investigator:
    if state.investigator is not None:
        return state.investigator
    try:
        from unwind.investigator import get_investigator  # noqa: PLC0415
    except ImportError as exc:
        raise UnwindError(
            "investigator unavailable: install the [llm] extra (uv pip install unwind[llm])"
        ) from exc
    provider = os.environ.get("UNWIND_LLM_PROVIDER", "openai")
    state.investigator = get_investigator(llm_provider=provider)
    return state.investigator


def _explanation_payload(exp: Explanation) -> dict[str, Any]:
    return {
        "summary": exp.summary,
        "findings": [
            {"model": f.model, "column": f.column, "value": f.value, "reason": f.reason}
            for f in exp.findings
        ],
    }
