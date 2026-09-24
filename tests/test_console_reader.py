"""Reading the runs directory, including the runs that did not finish."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from android_runner.console.reader import (
    AGENT,
    INCOMPLETE,
    INFRASTRUCTURE,
    ORACLE,
    PASS,
    RunStore,
)

CLICK = {
    "index": 0,
    "action": "click",
    "narration": "Tap the FYI app icon.",
    "arguments": {"action": "click", "coordinate": [228, 640]},
    "pixels": {"action": "click", "coordinate": [246, 1551]},
    "moved": True,
}
WAIT = {"index": 1, "action": "wait", "arguments": {"action": "wait", "time": 5}, "pixels": {}}


def write_turns(run_dir: Path, turns: list[dict[str, Any]]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    for turn in turns:
        path = run_dir / f"turn_{turn['index']:03d}.json"
        path.write_text(json.dumps(turn), encoding="utf-8")


def complete_run(
    root: Path,
    run_id: str,
    *,
    status: str = "verified",
    turns: list[dict[str, Any]] | None = None,
    checks: list[dict[str, Any]] | None = None,
) -> Path:
    turns = [CLICK] if turns is None else turns
    checks = [] if checks is None else checks
    run_dir = root / run_id
    write_turns(run_dir, turns)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "status": status,
                "verified": status == "verified",
                "error_class": None if status == "verified" else "agent",
                "instruction": "Open FYI",
                "success": "the project is sent",
                "detail": "done",
                "actions": 1,
                "waits": 0,
                "elapsed_s": 12.5,
                "turns": turns,
                "checks": checks,
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def test_a_finished_run_reads_back_from_run_json(tmp_path: Path) -> None:
    complete_run(tmp_path, "20260914T121427Z")
    summary = RunStore(tmp_path).summary("20260914T121427Z")
    assert summary is not None
    assert summary.outcome == PASS
    assert summary.complete is True
    assert summary.instruction == "Open FYI"
    assert summary.turn_count == 1
    assert summary.started_at == "2026-09-14T12:14:27Z"


def test_a_run_with_no_run_json_is_rebuilt_from_its_turns(tmp_path: Path) -> None:
    """The run that crashed is the one worth reading, so it is not skipped."""
    write_turns(tmp_path / "20260908T164612Z", [CLICK, WAIT])
    summary = RunStore(tmp_path).summary("20260908T164612Z")
    assert summary is not None
    assert summary.outcome == INCOMPLETE
    assert summary.complete is False
    assert summary.turn_count == 2
    assert summary.waits == 1
    assert summary.actions == 1
    # finish() is the only writer of the instruction, so a crash loses it.
    assert summary.instruction == ""


def test_a_run_directory_with_nothing_in_it_still_lists(tmp_path: Path) -> None:
    (tmp_path / "20260914T092631Z").mkdir(parents=True)
    summary = RunStore(tmp_path).summary("20260914T092631Z")
    assert summary is not None
    assert summary.outcome == INCOMPLETE
    assert summary.turn_count == 0


def test_one_unreadable_run_does_not_hide_the_others(tmp_path: Path) -> None:
    complete_run(tmp_path, "20260914T121427Z")
    broken = tmp_path / "20260914T100102Z"
    broken.mkdir(parents=True)
    (broken / "run.json").write_text("{ truncated", encoding="utf-8")

    summaries = RunStore(tmp_path).summaries()
    assert [s.id for s in summaries] == ["20260914T121427Z", "20260914T100102Z"]
    # A half-written run.json is reconstructed, not dropped.
    assert summaries[1].outcome == INCOMPLETE


def test_a_dead_server_never_reads_as_a_failed_task(tmp_path: Path) -> None:
    """The agent/infrastructure/oracle split is the whole point of the badge."""
    for status in ("device_error", "model_error", "oracle_error", "oracle_inconclusive"):
        complete_run(tmp_path, f"run_{status}", status=status)
    for status in (
        "parse_error",
        "stuck",
        "budget_exhausted",
        "timed_out",
        "actor_gave_up",
        "actor_claimed_success",
    ):
        complete_run(tmp_path, f"run_{status}", status=status)
    complete_run(tmp_path, "run_verified", status="verified")

    outcomes = {s.id: s.outcome for s in RunStore(tmp_path).summaries()}
    assert outcomes["run_verified"] == PASS
    assert outcomes["run_device_error"] == INFRASTRUCTURE
    assert outcomes["run_oracle_error"] == INFRASTRUCTURE
    # Its own badge. The harness worked; the judge answered and could not be
    # read, and a wrench next to that sends you to restart a healthy server.
    assert outcomes["run_oracle_inconclusive"] == ORACLE
    assert outcomes["run_stuck"] == AGENT
    # Nothing writes `timed_out` any more. Runs that already carry it still
    # have to read as a failed task rather than fall off the taxonomy.
    assert outcomes["run_timed_out"] == AGENT


def test_runs_are_listed_newest_first(tmp_path: Path) -> None:
    for run_id in ("20260908T095613Z", "20260914T121427Z", "20260911T075139Z"):
        complete_run(tmp_path, run_id)
    assert [s.id for s in RunStore(tmp_path).summaries()] == [
        "20260914T121427Z",
        "20260911T075139Z",
        "20260908T095613Z",
    ]


def test_a_missing_runs_directory_is_empty_not_an_error(tmp_path: Path) -> None:
    assert RunStore(tmp_path / "nope").summaries() == []


def test_an_out_dir_that_is_not_a_timestamp_has_no_start_time(tmp_path: Path) -> None:
    complete_run(tmp_path, "my-experiment")
    summary = RunStore(tmp_path).summary("my-experiment")
    assert summary is not None
    assert summary.started_at is None


def test_detail_carries_the_line_the_terminal_prints(tmp_path: Path) -> None:
    complete_run(tmp_path, "20260914T121427Z")
    detail = RunStore(tmp_path).detail("20260914T121427Z")
    assert detail is not None
    assert detail.turns[0]["line"] == (
        "click 228,640 of 1000 [mid-left], px 246,1551 moved"
    )


def test_detail_of_an_unfinished_run_comes_from_the_turn_files(tmp_path: Path) -> None:
    write_turns(tmp_path / "20260908T164612Z", [CLICK, WAIT])
    detail = RunStore(tmp_path).detail("20260908T164612Z")
    assert detail is not None
    assert len(detail.turns) == 2
    assert detail.checks == []
    assert "never written" in detail.detail


def test_detail_preserves_check_evidence_fields(tmp_path: Path) -> None:
    check = {
        "screenshot": "turn_000.after.png",
        "phase": "final",
        "turn": 0,
        "holds": False,
    }
    complete_run(tmp_path, "20260914T121427Z", checks=[check])
    detail = RunStore(tmp_path).detail("20260914T121427Z")
    assert detail is not None
    assert detail.checks == [check]


def test_an_unfinished_run_reads_incremental_checks(tmp_path: Path) -> None:
    run_dir = tmp_path / "20260908T164612Z"
    write_turns(run_dir, [CLICK])
    check = {
        "screenshot": "turn_000.after.png",
        "phase": "final",
        "turn": 0,
        "holds": False,
    }
    (run_dir / "check_000.json").write_text(json.dumps(check), encoding="utf-8")

    detail = RunStore(tmp_path).detail("20260908T164612Z")
    assert detail is not None
    assert detail.summary.complete is False
    assert detail.checks == [check]


def test_an_unknown_run_has_no_detail(tmp_path: Path) -> None:
    assert RunStore(tmp_path).detail("nope") is None


def test_only_artifacts_the_runner_writes_can_be_served(tmp_path: Path) -> None:
    run_dir = complete_run(tmp_path, "20260914T121427Z")
    (run_dir / "turn_000.marked.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    store = RunStore(tmp_path)

    assert store.artifact("20260914T121427Z", "turn_000.marked.png") is not None
    (run_dir / "check_000.json").write_text("{}", encoding="utf-8")
    assert store.artifact("20260914T121427Z", "check_000.json") is not None
    assert store.artifact("20260914T121427Z", "../secret.txt") is None
    assert store.artifact("20260914T121427Z", "../../etc/passwd") is None
    assert store.artifact("20260914T121427Z", "/etc/passwd") is None
    assert store.artifact("20260914T121427Z", "turn_000.png") is None  # not on disk
    assert store.artifact("20260914T121427Z", "notes.txt") is None


def test_a_summary_is_reread_when_the_run_changes(tmp_path: Path) -> None:
    """The cache is keyed on mtime, so a live run must not serve a stale row."""
    run_dir = complete_run(tmp_path, "20260914T121427Z", status="stuck")
    store = RunStore(tmp_path)
    assert store.summary("20260914T121427Z").outcome == AGENT  # type: ignore[union-attr]

    complete_run(tmp_path, "20260914T121427Z", status="verified")
    import os

    stamp = run_dir.stat().st_mtime + 10
    os.utime(run_dir, (stamp, stamp))
    assert store.summary("20260914T121427Z").outcome == PASS  # type: ignore[union-attr]


def test_delete_refuses_an_id_that_climbs_out_of_the_runs_directory(
    tmp_path: Path,
) -> None:
    """Local-only is not a reason to accept `..`, and rmtree is unforgiving."""
    runs = tmp_path / "runs"
    complete_run(runs, "20260914T121427Z")
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keep.txt").write_text("still here", encoding="utf-8")

    store = RunStore(runs)
    assert store.delete("../precious") is False
    assert store.delete("20260914T121427Z/..") is False
    assert (outside / "keep.txt").is_file()
    assert (runs / "20260914T121427Z").is_dir()


def test_delete_drops_the_cached_summary_with_the_directory(tmp_path: Path) -> None:
    """A summary kept after the run is gone would resurrect it in the listing."""
    complete_run(tmp_path, "20260914T121427Z")
    store = RunStore(tmp_path)
    assert store.summary("20260914T121427Z") is not None

    assert store.delete("20260914T121427Z") is True
    assert store.summary("20260914T121427Z") is None
    assert store.summaries() == []


# --- the checkpoint ladder ------------------------------------------------


def test_the_ladder_is_read_from_a_finished_run(tmp_path: Path) -> None:
    run_dir = complete_run(tmp_path, "20260914T121427Z")
    result = json.loads((run_dir / "run.json").read_text())
    result["checkpoints"] = [
        {"id": "logged-out", "condition": "the login screen", "met": True}
    ]
    (run_dir / "run.json").write_text(json.dumps(result), encoding="utf-8")

    detail = RunStore(tmp_path).detail("20260914T121427Z")
    assert detail is not None
    assert detail.as_dict()["checkpoints"][0]["id"] == "logged-out"


def test_a_run_from_before_checkpoints_has_an_empty_ladder(tmp_path: Path) -> None:
    """Every run already on disk. The key is absent, not false, so the reader
    supplies the empty list the console can render without a special case."""
    complete_run(tmp_path, "20260914T121427Z")
    detail = RunStore(tmp_path).detail("20260914T121427Z")
    assert detail is not None
    assert detail.as_dict()["checkpoints"] == []


def test_a_live_run_shows_its_ladder_filling(tmp_path: Path) -> None:
    """`run.json` is written once, at the end. Without this the ladder - the
    one view that answers "which step broke" - would appear only after the
    run it was being watched for is over."""
    run_dir = tmp_path / "20260914T121427Z"
    write_turns(run_dir, [CLICK])
    (run_dir / "case.json").write_text(
        json.dumps(
            {
                "name": "Log out",
                "instruction": "log out and back in",
                "success": "the chat screen is visible",
                "checkpoints": [
                    {"id": "logged-out", "condition": "the login screen", "after": "log ?out"},
                    {"id": "coachmark", "condition": "the tip is visible", "required": False},
                ],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "check_000.json").write_text(
        json.dumps(
            {
                "phase": "checkpoint",
                "checkpoint_id": "logged-out",
                "holds": True,
                "turn": 3,
                "screenshot": "turn_003.after.png",
            }
        ),
        encoding="utf-8",
    )

    detail = RunStore(tmp_path).detail("20260914T121427Z")
    assert detail is not None
    rungs = detail.as_dict()["checkpoints"]
    assert [r["id"] for r in rungs] == ["logged-out", "coachmark"]
    assert (rungs[0]["met"], rungs[0]["turn"], rungs[0]["polls"]) == (True, 3, 1)
    assert rungs[0]["screenshot"] == "turn_003.after.png"
    # Not yet asked, which is not the same as asked and not found.
    assert (rungs[1]["met"], rungs[1]["triggered"], rungs[1]["required"]) == (
        False,
        False,
        False,
    )


def test_a_live_run_without_a_case_file_has_no_ladder(tmp_path: Path) -> None:
    """A run typed straight into the terminal, and every run on disk today."""
    write_turns(tmp_path / "20260914T121427Z", [CLICK])
    detail = RunStore(tmp_path).detail("20260914T121427Z")
    assert detail is not None
    assert detail.as_dict()["checkpoints"] == []
