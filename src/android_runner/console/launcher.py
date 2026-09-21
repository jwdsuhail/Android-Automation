"""Starting a run from the console.

A subprocess, not a thread. `model-run` drives a device over ADB, and an ADB
call that never returns would take the console down with it if it shared the
process. Out of process, a wedged or crashed run costs exactly one run.

Nothing here parses the child's output. It goes directly to `console.log`, and
a separate tail echoes those same bytes to the terminal running `model-console`.
The run writes `turn_NNN.json` as it goes and `reader.py` reconstructs progress
from those files - that is what the `incomplete` outcome exists for.
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
from android_runner.runner import new_run_id

# The child is `python -m android_runner.cli`, not `model-run`. The console may
# be running from a virtualenv whose scripts are not on PATH, and sys.executable
# is the one interpreter known to have this package installed.
_MODULE = "android_runner.cli"

# The child flushes every turn line, so this is the maximum extra delay before
# that line appears in model-console's terminal. It does not delay the child.
ECHO_POLL_S = 0.1
ECHO_CHUNK_BYTES = 64 * 1024


def _terminal_write(data: bytes) -> None:
    """Write child bytes to stderr without changing or reassembling lines."""
    binary = getattr(sys.stderr, "buffer", None)
    if binary is not None:
        binary.write(data)
        binary.flush()
        return
    # `sys.stderr` can be replaced by a text-only stream in an embedding host.
    sys.stderr.write(data.decode("utf-8", errors="replace"))
    sys.stderr.flush()


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
        return new_run_id()

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

        finished = threading.Event()
        # Tailing the file rather than piping the child keeps model-run
        # independent: if this console exits, the child can still finish and
        # leave its complete diagnostic log behind.
        threading.Thread(
            target=self._echo,
            args=(out_dir / "console.log", launch, finished),
            name=f"echo-{run_id}",
            daemon=True,
        ).start()
        # Reaped off the lock: waiting for a run to finish while holding it
        # would block every other request for the length of the run.
        threading.Thread(
            target=self._reap,
            args=(process, launch, log, finished),
            name=f"reap-{run_id}",
            daemon=True,
        ).start()
        return launch

    def _echo(self, path: Path, launch: Launch, finished: threading.Event) -> None:
        """Tail one log until the process has exited and every byte is drained."""
        _terminal_write(f"\n[run {launch.run_id}] started\n".encode())
        try:
            with path.open("rb") as source:
                while True:
                    chunk = source.read(ECHO_CHUNK_BYTES)
                    if chunk:
                        _terminal_write(chunk)
                        continue
                    if finished.is_set():
                        break
                    finished.wait(ECHO_POLL_S)
        except OSError as exc:
            _terminal_write(
                f"\n[run {launch.run_id}] log unavailable: {exc}\n".encode()
            )
        finally:
            code = launch.exit_code
            label = "unknown" if code is None else str(code)
            _terminal_write(f"\n[run {launch.run_id}] exited {label}\n".encode())

    def _reap(
        self,
        process: subprocess.Popen[bytes],
        launch: Launch,
        log: Any,
        finished: threading.Event,
    ) -> None:
        try:
            code = process.wait()
            with self._lock:
                launch.exit_code = code
        finally:
            # Signal only after closing the writer. The echo thread then drains
            # bytes flushed by Python during interpreter shutdown before it
            # observes EOF and exits.
            try:
                log.close()
            finally:
                finished.set()
