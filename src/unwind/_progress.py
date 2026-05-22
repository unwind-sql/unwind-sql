"""Observable events emitted by `Project.run`, plus the default TTY renderer.

The runner emits a stream of `RunEvent`s at every scheduling boundary so a
caller can build a live UI without poking at private state. `ProgressCallback`
is the public type for that observer.

When the caller doesn't pass an explicit `on_event=`, `Project.run` looks up
`auto_progress()` — which returns a `RichProgressRenderer` if stderr is a TTY
AND `rich` is importable AND the user hasn't set `UNWIND_NO_PROGRESS=1`.
Returns `None` otherwise; the runner treats `None` as "no progress UI".

The renderer is intentionally kept in a sibling module (`_progress_rich.py`)
so that this file imports stdlib only — a project that runs without `rich`
installed still pays no import cost.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

EventKind = Literal["start", "model_start", "model_done", "model_skipped", "done"]


@dataclass(frozen=True, slots=True)
class RunEvent:
    """One observation of the runner state.

    `kind == "start"`: emitted once before any model. `total` carries the
    model count; `completed` is 0 and `in_flight` is empty.

    `kind == "model_start"`: emitted just after a model is submitted for
    execution. `name` is set; `in_flight` *includes* `name` (the snapshot
    reflects state after the event is applied).

    `kind == "model_done"`: emitted when a model finished successfully.
    `name`, `duration_s`, `row_count` are set; `in_flight` no longer
    contains `name`; `completed` already counts this model.

    `kind == "model_skipped"`: a disabled leaf model with no parents — no
    materialization happened. `name` is set; `completed` counts it.

    `kind == "done"`: emitted once after the last model. `completed == total`.

    `elapsed_s` is wall-clock since the run started, identical to the value
    `RunResult.total_duration_s` will hold at the `done` event.
    """

    kind: EventKind
    name: str | None = None
    completed: int = 0
    total: int = 0
    in_flight: tuple[str, ...] = ()
    duration_s: float | None = None
    row_count: int | None = None
    elapsed_s: float = 0.0


ProgressCallback = Callable[[RunEvent], None]


def auto_progress() -> ProgressCallback | None:
    """Return the default renderer when the environment supports it.

    Conditions: stderr is a TTY, `rich` is importable, and the user has not
    opted out via `UNWIND_NO_PROGRESS=1`. Returns `None` otherwise — the
    runner treats `None` as "no progress UI", preserving the silent default
    of `Project.run` for pipelines, CI, and test runners.
    """
    if os.environ.get("UNWIND_NO_PROGRESS"):
        return None
    if not sys.stderr.isatty():
        return None
    try:
        from unwind._progress_rich import RichProgressRenderer  # noqa: PLC0415
    except ImportError:
        return None
    return RichProgressRenderer()
