"""The `runs/` directory, read as data.

The filesystem is the source of truth. There is no database and no index: a
run directory is self-describing, and anything derived from it here can be
recomputed by deleting nothing.

The one thing this module refuses to do is skip a directory it cannot parse.
A run that died before `finish()` wrote `run.json` is exactly the run worth
looking at, so it is reconstructed from the turn records instead of vanishing
from the list.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from android_runner import format as fmt
from android_runner.runner import error_class

# Every artifact `runner.run()` can write, and nothing else. The file endpoint
# matches against this rather than joining user input onto a path.
ARTIFACT = re.compile(
    r"^(?:entry\.png|run\.json"
    r"|turn_\d{3}(?:\.marked|\.after)?\.png"
    r"|turn_\d{3}\.raw\.txt"
    r"|turn_\d{3}\.json"
    r"|verify_\d{3}\.png)$"
)

_STAMP = "%Y%m%dT%H%M%SZ"

# The outcome a badge is drawn from. `error_class` already owns the split that
# matters - a dead server is not a failed task - so this only adds the state
# that exists on disk but never inside a result: no result at all.
PASS = "pass"
AGENT = "agent"
INFRASTRUCTURE = "infrastructure"
INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class RunSummary:
    id: str
    started_at: str | None
    status: str
    verified: bool
    error_class: str | None
    outcome: str
    instruction: str
    success: str | None
    actions: int
    waits: int
    elapsed_s: float | None
    turn_count: int
    complete: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunDetail:
    summary: RunSummary
    detail: str
    turns: list[dict[str, Any]]
    checks: list[dict[str, Any]]
    warmup_ms: float | None = None
    warmup_error: str | None = None
    artifacts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        payload = self.summary.as_dict()
        payload.update(
            {
                "detail": self.detail,
                "turns": self.turns,
                "checks": self.checks,
                "warmup_ms": self.warmup_ms,
                "warmup_error": self.warmup_error,
                "artifacts": self.artifacts,
            }
        )
        return payload


def _started_at(run_id: str) -> str | None:
    """The directory name is a UTC stamp, unless `--out` named it something else."""
    try:
        parsed = datetime.strptime(run_id, _STAMP).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return parsed.isoformat().replace("+00:00", "Z")


def _load(path: Path) -> dict[str, Any] | None:
    """A JSON file, or None if it is absent, truncated or not an object.

    A run killed mid-write leaves a half-written record. That is a reason to
    show the rest of the run, not to fail the request for it.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _turn_files(run_dir: Path) -> list[Path]:
    return sorted(run_dir.glob("turn_[0-9][0-9][0-9].json"))


def _outcome(status: str, verified: bool, complete: bool) -> str:
    if not complete:
        return INCOMPLETE
    if verified:
        return PASS
    return INFRASTRUCTURE if error_class(status) == INFRASTRUCTURE else AGENT


def _decorate(turn: dict[str, Any]) -> dict[str, Any]:
    """Add the rendered action line the terminal prints.

    Computed here rather than in the browser so that `format.turn_line` stays
    the single definition of what an action looks like written down.
    """
    turn = dict(turn)
    turn["line"] = fmt.turn_line(turn)
    return turn


def _reconstructed(run_id: str, run_dir: Path) -> RunSummary:
    """A summary for a run whose `run.json` was never written.

    `finish()` is the only writer of the instruction and the success
    condition, so neither survives a crash. Everything else is recoverable
    from the turn records, and saying so is better than hiding the run.
    """
    turns = [t for t in (_load(p) for p in _turn_files(run_dir)) if t is not None]
    waits = sum(1 for t in turns if t.get("action") == "wait")
    return RunSummary(
        id=run_id,
        started_at=_started_at(run_id),
        status=INCOMPLETE,
        verified=False,
        error_class=None,
        outcome=INCOMPLETE,
        instruction="",
        success=None,
        actions=len(turns) - waits,
        waits=waits,
        elapsed_s=None,
        turn_count=len(turns),
        complete=False,
    )


def _summarize(run_id: str, result: dict[str, Any]) -> RunSummary:
    status = str(result.get("status", INCOMPLETE))
    verified = bool(result.get("verified"))
    turns = result.get("turns")
    return RunSummary(
        id=run_id,
        started_at=_started_at(run_id),
        status=status,
        verified=verified,
        error_class=result.get("error_class"),
        outcome=_outcome(status, verified, complete=True),
        instruction=str(result.get("instruction", "")),
        success=result.get("success"),
        actions=int(result.get("actions", 0)),
        waits=int(result.get("waits", 0)),
        elapsed_s=result.get("elapsed_s"),
        turn_count=len(turns) if isinstance(turns, list) else 0,
        complete=True,
    )


class RunStore:
    """Reads a runs directory, caching each summary against its mtime.

    `run.json` embeds every turn, so listing twenty runs re-reads every turn
    of every one of them. The cache is keyed on the directory's mtime because
    that is what changes when a run writes another artifact into it.
    """

    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir
        self._cache: dict[str, tuple[float, RunSummary]] = {}

    def ids(self) -> list[str]:
        """Run directory names, newest first.

        The default name is a UTC stamp, so lexical order is chronological.
        A `--out` directory that is not a stamp sorts by name among them.
        """
        if not self.runs_dir.is_dir():
            return []
        return sorted(
            (p.name for p in self.runs_dir.iterdir() if p.is_dir()), reverse=True
        )

    def summary(self, run_id: str) -> RunSummary | None:
        run_dir = self.runs_dir / run_id
        if not run_dir.is_dir():
            return None
        try:
            stamp = run_dir.stat().st_mtime
        except OSError:
            return None
        cached = self._cache.get(run_id)
        if cached is not None and cached[0] == stamp:
            return cached[1]

        result = _load(run_dir / "run.json")
        found = (
            _summarize(run_id, result)
            if result is not None
            else _reconstructed(run_id, run_dir)
        )
        self._cache[run_id] = (stamp, found)
        return found

    def summaries(self) -> list[RunSummary]:
        found = (self.summary(run_id) for run_id in self.ids())
        return [s for s in found if s is not None]

    def detail(self, run_id: str) -> RunDetail | None:
        run_dir = self.runs_dir / run_id
        summary = self.summary(run_id)
        if summary is None:
            return None

        result = _load(run_dir / "run.json")
        if result is None:
            turns = [t for t in (_load(p) for p in _turn_files(run_dir)) if t is not None]
            checks: list[dict[str, Any]] = []
            detail = "run.json was never written - this run did not reach the end"
            warmup_ms = warmup_error = None
        else:
            raw_turns = result.get("turns")
            turns = raw_turns if isinstance(raw_turns, list) else []
            raw_checks = result.get("checks")
            checks = raw_checks if isinstance(raw_checks, list) else []
            detail = str(result.get("detail", ""))
            warmup_ms = result.get("warmup_ms")
            warmup_error = result.get("warmup_error")

        return RunDetail(
            summary=summary,
            detail=detail,
            turns=[_decorate(t) for t in turns],
            checks=checks,
            warmup_ms=warmup_ms,
            warmup_error=warmup_error,
            artifacts=sorted(
                p.name for p in run_dir.iterdir() if ARTIFACT.match(p.name)
            ),
        )

    def artifact(self, run_id: str, name: str) -> Path | None:
        """A file inside one run directory, or None.

        The name is matched against the set of artifacts the runner writes
        before it is joined to anything, and the result is checked to be
        inside the run directory afterwards. Local-only is not a reason to
        serve a traversal.
        """
        if not ARTIFACT.match(name):
            return None
        run_dir = (self.runs_dir / run_id).resolve()
        if not run_dir.is_dir():
            return None
        path = (run_dir / name).resolve()
        if path.parent != run_dir or not path.is_file():
            return None
        return path
