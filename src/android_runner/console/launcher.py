"""Starting a run from the console.

A subprocess, not a thread. `model-run` drives a device over ADB, and an ADB
call that never returns would take the console down with it if it shared the
process. Out of process, a wedged or crashed run costs exactly one run.

Nothing here parses the child's output. The run writes `turn_NNN.json` as it
goes and `reader.py` already reconstructs a run from those files - that is what
the `incomplete` outcome exists for. A run in progress is an unfinished run, so
progress is read from the directory, and a run started at the terminal streams
into the browser on the same path as one started here.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from android_runner.cases import Case

# The child is `python -m android_runner.cli`, not `model-run`. The console may
# be running from a virtualenv whose scripts are not on PATH, and sys.executable
# is the one interpreter known to have this package installed.
_MODULE = "android_runner.cli"


class Busy(RuntimeError):
    """A run is already in flight.

    One emulator cannot run two tests at once: they would each act on the
    other's screen, and both records would be fiction. This is a refusal, not a
    queue - the caller decides whether to wait.
    """

    def __init__(self, run_id: str) -> None:
        super().__init__(f"a run is already in progress: {run_id}")
        self.run_id = run_id


@dataclass
class Launch:
    """One spawned run, and what is known about it so far."""

    run_id: str
    case_id: str
    started_at: str
    pid: int
    exit_code: int | None = None

    @property
    def running(self) -> bool:
        return self.exit_code is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "case_id": self.case_id,
            "started_at": self.started_at,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "running": self.running,
        }


class Launcher:
    """Spawns runs and remembers them for as long as the console lives.

    The registry is in memory on purpose. It holds process state - a pid, an
    exit code - which does not outlive the process that owns it. Everything
    worth keeping is already on disk in the run directory.
    """

    def __init__(self, runs_dir: Path, cases_dir: Path) -> None:
        self.runs_dir = Path(runs_dir)
        self.cases_dir = Path(cases_dir)
        self._lock = threading.Lock()
        self._launches: dict[str, Launch] = {}

    def _stamp(self) -> str:
        """The same UTC run id `cli.py` would have chosen.

        Generated here and passed as `--out` so the id is known before the child
        starts, which is what lets the caller be handed a URL immediately.
        """
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def active(self) -> Launch | None:
        with self._lock:
            return self._active_locked()

    def _active_locked(self) -> Launch | None:
        for launch in self._launches.values():
            if launch.running:
                return launch
        return None

    def status(self, run_id: str) -> Launch | None:
        with self._lock:
            return self._launches.get(run_id)

    def launch(self, case: Case) -> Launch:
        """Start `case`, or raise `Busy` if something is already running."""
        with self._lock:
            in_flight = self._active_locked()
            if in_flight is not None:
                raise Busy(in_flight.run_id)

            run_id = self._stamp()
            # Two launches inside one second would collide on the stamp and the
            # second would write its turns into the first one's directory.
            while (self.runs_dir / run_id).exists() or run_id in self._launches:
                run_id += "-2"

            out_dir = self.runs_dir / run_id
            out_dir.mkdir(parents=True, exist_ok=True)

            case_path = self.cases_dir / f"{case.id}.json"
            command = [
                sys.executable,
                "-m",
                _MODULE,
                "--case",
                str(case_path),
                "--out",
                str(out_dir),
            ]

            # The failure that happens before the first turn - a dead device, a
            # refused model connection - leaves no turn record to read. Without
            # this file the console would show an empty run and no reason.
            log = (out_dir / "console.log").open("wb")
            process = subprocess.Popen(
                command,
                cwd=self.runs_dir.parent,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )

            launch = Launch(
                run_id=run_id,
                case_id=case.id,
                started_at=datetime.now(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
                pid=process.pid,
            )
            self._launches[run_id] = launch

        # Reaped off the lock: waiting for a run to finish while holding it
        # would block every other request for the length of the run.
        threading.Thread(
            target=self._reap,
            args=(process, launch, log),
            name=f"reap-{run_id}",
            daemon=True,
        ).start()
        return launch

    def _reap(self, process: subprocess.Popen[bytes], launch: Launch, log: Any) -> None:
        code = process.wait()
        log.close()
        with self._lock:
            launch.exit_code = code
