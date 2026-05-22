"""Rich-based renderer for `Project.run` progress events.

Loaded lazily by `auto_progress()` so the package doesn't pay rich's import
cost (or fail) when the optional `[progress]` extra isn't installed.

The display is a `rich.live.Live` group containing:
    1. a `Progress` bar (spinner + bar + N/total + percent + elapsed)
    2. a "running: <names>" line listing all in-flight models
    3. a "last: <name> — R rows in T ms" line for the most recent completion

On `done` we stop the live display; the bar stays on screen as a final
record of what ran.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console, Group
from rich.live import Live
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.text import Text

if TYPE_CHECKING:
    from rich.console import RenderableType

    from unwind._progress import RunEvent


class RichProgressRenderer:
    """Stateful callable that draws a live progress UI for one `Project.run`.

    One instance handles one run. Hold no state across runs — the runner
    obtains a fresh instance via `auto_progress()` on every call.
    """

    def __init__(self) -> None:
        self._console: Console | None = None
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None
        self._live: Live | None = None
        # Most recent completion: (name, row_count, duration_s)
        self._last_done: tuple[str, int, float] | None = None

    def __call__(self, event: RunEvent) -> None:
        if event.kind == "start":
            self._start(event.total)
            self._refresh(event)
            return

        if self._progress is None:
            # Defensive: out-of-order event before "start" — silently ignore
            # rather than crash the runner over a UI hiccup.
            return

        if event.kind in ("model_done", "model_skipped"):
            assert self._task_id is not None  # set by _start before first non-start event
            self._progress.advance(self._task_id)
            if event.kind == "model_done" and event.name is not None:
                self._last_done = (
                    event.name,
                    event.row_count or 0,
                    event.duration_s or 0.0,
                )
        self._refresh(event)
        if event.kind == "done":
            self._stop()

    def _start(self, total: int) -> None:
        self._console = Console(stderr=True)
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]running models"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=self._console,
            transient=False,
        )
        self._task_id = self._progress.add_task("models", total=total)
        self._live = Live(
            self._build_group(),
            console=self._console,
            refresh_per_second=10,
            transient=False,
        )
        self._live.start()

    def _refresh(self, event: RunEvent) -> None:
        if self._live is not None:
            self._live.update(self._build_group(event))

    def _stop(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def _build_group(self, event: RunEvent | None = None) -> RenderableType:
        assert self._progress is not None  # _start sets it before _build_group runs
        in_flight = event.in_flight if event is not None else ()
        running_line = (
            Text.assemble(("in flight: ", "dim"), ", ".join(in_flight))
            if in_flight
            else Text("in flight: —", style="dim")
        )
        last_line = (
            Text.assemble(
                ("last: ", "dim"),
                f"{self._last_done[0]} — ",
                (f"{self._last_done[1]:,} rows ", "bold"),
                f"in {self._last_done[2] * 1000:.1f} ms",
            )
            if self._last_done is not None
            else Text("last: —", style="dim")
        )
        return Group(self._progress, running_line, last_line)
