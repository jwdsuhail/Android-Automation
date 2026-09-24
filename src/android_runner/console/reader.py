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
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from android_runner import format as fmt
from android_runner.runner import (
    AGENT,
    INFRASTRUCTURE,
    ORACLE,
    RUN_ID_STAMP,
    error_class,
)

# Every artifact `runner.run()` can write, and nothing else. The file endpoint
# matches against this rather than joining user input onto a path.
ARTIFACT = re.compile(
    r"^(?:entry\.png|run\.json|case\.json|console\.log"
    r"|check_\d{3}\.json"
    r"|turn_\d{3}(?:\.marked|\.after)?\.png"
    r"|turn_\d{3}\.raw\.txt"
    r"|turn_\d{3}\.json"
    r"|verify_\d{3}\.png)$"
)


# The outcome a badge is drawn from. `error_class` already owns the split that
# matters - a dead server is not a failed task - so this only adds the state
# that exists on disk but never inside a result: no result at all.
# AGENT, INFRASTRUCTURE and ORACLE are re-exported from `runner` rather than
# re-declared: they are exactly what `error_class` returns, and a badge that
# disagreed with the field it is drawn from would be invisible until it was
# wrong on screen. ORACLE earns its own badge rather than the wrench - a judge
# that answered and was unusable is not a broken harness, and filing it as one
# made the board over-report breakage.
PASS = "pass"
# The one outcome with no counterpart in `error_class`: there is no result on
# disk at all.
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
    # From the `case.json` snapshot the run was started with, absent for a run
    # typed straight into the terminal.
    case_id: str | None
    case_name: str | None
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
    # The named steps and whether the run went through them. Empty for every
    # run recorded before checkpoints existed, and for every case without any.
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
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
                "checkpoints": self.checkpoints,
                "warmup_ms": self.warmup_ms,
                "warmup_error": self.warmup_error,
                "artifacts": self.artifacts,
            }
        )
        return payload


def _started_at(run_id: str) -> str | None:
    """The directory name is a UTC stamp, unless `--out` named it something else."""
    try:
        parsed = datetime.strptime(run_id, RUN_ID_STAMP).replace(tzinfo=timezone.utc)
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


def _check_files(run_dir: Path) -> list[Path]:
    """Incremental oracle records written before run.json exists."""
    return sorted(run_dir.glob("check_[0-9][0-9][0-9].json"))


def _outcome(status: str, verified: bool, complete: bool) -> str:
    if not complete:
        return INCOMPLETE
    if verified:
        return PASS
    failed = error_class(status)
    if failed in (INFRASTRUCTURE, ORACLE):
        return failed
    return AGENT


def _decorate(turn: dict[str, Any]) -> dict[str, Any]:
    """Add the rendered action line the terminal prints.

    Computed here rather than in the browser so that `format.turn_line` stays
    the single definition of what an action looks like written down.
    """
    turn = dict(turn)
    turn["line"] = fmt.turn_line(turn)
    return turn


def _case(run_dir: Path) -> dict[str, Any]:
    """The `case.json` snapshot, or an empty mapping.

    Written before the first turn by whoever started the run, so unlike
    `run.json` it is present from the beginning - which is the only reason a
    run still in progress can say what it is trying to do.
    """
    return _load(run_dir / "case.json") or {}


def _reconstructed(run_id: str, run_dir: Path) -> RunSummary:
    """A summary for a run whose `run.json` was never written.

    `finish()` is the only writer of the instruction and the success
    condition, so neither survives a crash. Everything else is recoverable
    from the turn records, and saying so is better than hiding the run.

    A run started from a case is the exception: its snapshot carries both, so a
    crashed or still-running case run is described rather than blank.
    """
    turns = [t for t in (_load(p) for p in _turn_files(run_dir)) if t is not None]
    waits = sum(1 for t in turns if t.get("action") == "wait")
    case = _case(run_dir)
    return RunSummary(
        id=run_id,
        started_at=_started_at(run_id),
        status=INCOMPLETE,
        verified=False,
        error_class=None,
        outcome=INCOMPLETE,
        instruction=str(case.get("instruction", "")),
        success=case.get("success"),
        case_id=case.get("id"),
        case_name=case.get("name"),
        actions=len(turns) - waits,
        waits=waits,
        elapsed_s=None,
        turn_count=len(turns),
        complete=False,
    )


def _summarize(run_id: str, run_dir: Path, result: dict[str, Any]) -> RunSummary:
    status = str(result.get("status", INCOMPLETE))
    verified = bool(result.get("verified"))
    turns = result.get("turns")
    case = _case(run_dir)
    return RunSummary(
        id=run_id,
        started_at=_started_at(run_id),
        status=status,
        verified=verified,
        error_class=result.get("error_class"),
        outcome=_outcome(status, verified, complete=True),
        instruction=str(result.get("instruction", "")),
        success=result.get("success"),
        case_id=case.get("id"),
        case_name=case.get("name"),
        actions=int(result.get("actions", 0)),
        waits=int(result.get("waits", 0)),
        elapsed_s=result.get("elapsed_s"),
        turn_count=len(turns) if isinstance(turns, list) else 0,
        complete=True,
    )


def _ladder(run_dir: Path, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rungs of a run that has not written `run.json` yet.

    The same shape `finish()` writes, rebuilt from the case the run was started
    with and the checks on disk so far, so a live run shows its ladder filling
    rather than nothing at all until the end.

    `triggered` here means "was asked", which this side of `run.json` is the
    only evidence of a trigger there is. The two part company in one place: a
    rung whose trigger fired with no polls left is recorded by the runner and
    cannot be seen from here. That resolves the moment the run ends.
    """
    case = _load(run_dir / "case.json") or {}
    specs = case.get("checkpoints")
    if not isinstance(specs, list):
        return []

    rungs: list[dict[str, Any]] = []
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        rung_id = str(spec.get("id") or "")
        asked = [check for check in checks if check.get("checkpoint_id") == rung_id]
        met = next((check for check in asked if check.get("holds") is True), None)
        rungs.append(
            {
                "id": rung_id,
                "condition": str(spec.get("condition") or ""),
                "after": spec.get("after"),
                "required": bool(spec.get("required", True)),
                "met": met is not None,
                "triggered": bool(asked),
                "polls": len(asked),
                "turn": met.get("turn") if met else None,
                "screenshot": met.get("screenshot") if met else None,
                # Left to the run to write. The oracle's own justification is
                # one click away on the check it came from, and putting it here
                # would read as the runner's verdict on the rung.
                "detail": "",
            }
        )
    return rungs


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
            _summarize(run_id, run_dir, result)
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
            checks = [
                check
                for check in (_load(path) for path in _check_files(run_dir))
                if check is not None
            ]
            detail = "run.json was never written - this run did not reach the end"
            checkpoints = _ladder(run_dir, checks)
            warmup_ms = warmup_error = None
        else:
            raw_turns = result.get("turns")
            turns = raw_turns if isinstance(raw_turns, list) else []
            raw_checks = result.get("checks")
            checks = raw_checks if isinstance(raw_checks, list) else []
            raw_rungs = result.get("checkpoints")
            checkpoints = raw_rungs if isinstance(raw_rungs, list) else []
            detail = str(result.get("detail", ""))
            warmup_ms = result.get("warmup_ms")
            warmup_error = result.get("warmup_error")

        return RunDetail(
            summary=summary,
            detail=detail,
            turns=[_decorate(t) for t in turns],
            checks=checks,
            checkpoints=checkpoints,
            warmup_ms=warmup_ms,
            warmup_error=warmup_error,
            artifacts=sorted(
                p.name for p in run_dir.iterdir() if ARTIFACT.match(p.name)
            ),
        )

    def delete(self, run_id: str) -> bool:
        """Remove one run directory and everything inside it.

        The id is resolved and checked to be a direct child of the runs
        directory before anything is unlinked, for the same reason `artifact`
        checks one: local-only is not a reason to accept `..`. A run is the
        only thing here with no other copy, so this is the one operation in
        this module that cannot be undone by re-reading the disk.
        """
        root = self.runs_dir.resolve()
        run_dir = (self.runs_dir / run_id).resolve()
        if run_dir.parent != root or not run_dir.is_dir():
            return False
        shutil.rmtree(run_dir)
        self._cache.pop(run_id, None)
        return True

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
