"""Starting runs: one at a time, out of process, recorded on disk."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from android_runner.cases import Case, CaseStore
from android_runner.console import launcher as launcher_module
from android_runner.console.launcher import Busy, Launcher


class FakeProcess:
    """A child that exits when the test says so, not when it feels like it."""

    def __init__(self, command: list[str], **kwargs: Any) -> None:
        self.command = command
        self.kwargs = kwargs
        self.pid = 4242
        self.released = threading.Event()
        self.code = 0
        self.exit_output = b""

    def wait(self) -> int:
        self.released.wait(timeout=5)
        if self.exit_output:
            self.kwargs["stdout"].write(self.exit_output)
            self.kwargs["stdout"].flush()
        return self.code

    def finish(self, code: int = 0) -> None:
        self.code = code
        self.released.set()


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[FakeProcess]:
    made: list[FakeProcess] = []

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        process = FakeProcess(command, **kwargs)
        made.append(process)
        return process

    monkeypatch.setattr(launcher_module.subprocess, "Popen", fake_popen)
    return made


def a_case(tmp_path: Path) -> Case:
    store = CaseStore(tmp_path / "cases")
    return store.save(
        Case(id="audio-metadata", name="Audio metadata", instruction="Open the chat")
    )


def build(tmp_path: Path) -> Launcher:
    return Launcher(tmp_path / "runs", tmp_path / "cases")


def terminal_until(capfd: pytest.CaptureFixture[str], marker: str) -> str:
    """Collect threaded stderr until `marker` arrives, without a blind sleep."""
    output = ""
    for _ in range(500):
        output += capfd.readouterr().err
        if marker in output:
            return output
        threading.Event().wait(0.01)
    return output


def test_a_launch_names_its_run_before_the_child_starts(
    tmp_path: Path, spawned: list[FakeProcess]
) -> None:
    """The caller is handed a URL immediately, so the id cannot come later."""
    case = a_case(tmp_path)
    launch = build(tmp_path).launch(case)

    assert launch.run_id.endswith("Z")
    assert launch.case_id == "audio-metadata"
    assert launch.running is True
    assert (tmp_path / "runs" / launch.run_id).is_dir()
    spawned[0].finish()


def test_the_child_is_this_interpreter_not_a_script_on_path(
    tmp_path: Path, spawned: list[FakeProcess]
) -> None:
    """`model-run` may not be on PATH; sys.executable certainly has the package."""
    import sys

    case = a_case(tmp_path)
    launch = build(tmp_path).launch(case)

    command = spawned[0].command
    assert command[0] == sys.executable
    assert command[1:3] == ["-m", "android_runner.cli"]
    assert str(tmp_path / "cases" / "audio-metadata.json") in command
    assert str(tmp_path / "runs" / launch.run_id) in command
    spawned[0].finish()


def test_output_is_captured_for_the_failure_that_writes_no_turn(
    tmp_path: Path, spawned: list[FakeProcess]
) -> None:
    """A dead device fails before turn 0, leaving nothing else to read."""
    case = a_case(tmp_path)
    launch = build(tmp_path).launch(case)
    assert (tmp_path / "runs" / launch.run_id / "console.log").is_file()
    spawned[0].finish()


def test_the_log_is_echoed_to_the_consoles_terminal(
    tmp_path: Path,
    spawned: list[FakeProcess],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Browser-started runs should read like model-run in model-console's terminal."""
    monkeypatch.setattr(launcher_module, "ECHO_POLL_S", 0)
    launch = build(tmp_path).launch(a_case(tmp_path))
    line = b'[0] Tap "New Project".\n     -> click 657,303 moved\n'
    spawned[0].kwargs["stdout"].write(line)
    spawned[0].kwargs["stdout"].flush()
    spawned[0].finish()

    output = terminal_until(capfd, f"[run {launch.run_id}] exited 0")
    assert line.decode() in output
    assert (tmp_path / "runs" / launch.run_id / "console.log").read_bytes() == line


def test_output_flushed_during_process_exit_is_still_echoed(
    tmp_path: Path,
    spawned: list[FakeProcess],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The final JSON summary lands at interpreter exit and must be drained."""
    monkeypatch.setattr(launcher_module, "ECHO_POLL_S", 0)
    launch = build(tmp_path).launch(a_case(tmp_path))
    spawned[0].exit_output = b'{"status": "verified"}\nwrote runs/example\n'
    spawned[0].finish()

    output = terminal_until(capfd, f"[run {launch.run_id}] exited 0")
    assert '{"status": "verified"}' in output
    assert "wrote runs/example" in output


def test_a_second_launch_is_refused_while_the_first_runs(
    tmp_path: Path, spawned: list[FakeProcess]
) -> None:
    case = a_case(tmp_path)
    runner = build(tmp_path)
    first = runner.launch(case)

    with pytest.raises(Busy) as raised:
        runner.launch(case)
    assert raised.value.run_id == first.run_id
    assert runner.active() is not None

    spawned[0].finish()


def test_the_next_launch_is_allowed_once_the_first_has_exited(
    tmp_path: Path, spawned: list[FakeProcess]
) -> None:
    case = a_case(tmp_path)
    runner = build(tmp_path)
    first = runner.launch(case)
    spawned[0].finish(code=1)

    # The reaper runs off the lock, so wait for it rather than sleeping blind.
    for _ in range(500):
        if not runner.status(first.run_id).running:  # type: ignore[union-attr]
            break
        threading.Event().wait(0.01)

    assert runner.status(first.run_id).exit_code == 1  # type: ignore[union-attr]
    assert runner.active() is None

    second = runner.launch(case)
    assert second.run_id != first.run_id
    spawned[1].finish()


def test_two_launches_in_one_second_do_not_share_a_directory(
    tmp_path: Path, spawned: list[FakeProcess], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stamp has one-second resolution; two runs in it would interleave turns."""
    case = a_case(tmp_path)
    runner = build(tmp_path)
    monkeypatch.setattr(runner, "_stamp", lambda: "20260915T120000Z")

    first = runner.launch(case)
    spawned[0].finish()
    for _ in range(500):
        if not runner.status(first.run_id).running:  # type: ignore[union-attr]
            break
        threading.Event().wait(0.01)

    second = runner.launch(case)
    assert first.run_id != second.run_id
    spawned[1].finish()
