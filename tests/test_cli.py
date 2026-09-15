"""How a run result becomes an exit code and a JSON footer, and how a case
file, the command line, or both decide what actually runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from android_runner.cli import _exit_code, _oracle_summary, _resolve_case



def test_only_a_verified_run_exits_zero() -> None:
    assert _exit_code({"verified": True, "error_class": None}) == 0


def test_a_failed_task_and_a_dead_harness_do_not_share_an_exit_code() -> None:
    """Exit 1 means the agent did not get there. Exit 2 means the run carries
    no signal about the agent at all."""
    assert _exit_code({"verified": False, "error_class": "agent"}) == 1
    assert _exit_code({"verified": False, "error_class": "infrastructure"}) == 2


def test_oracle_summary_shows_both_answers_when_they_agree() -> None:
    result = {
        "checks": [
            {
                "holds": None,
                "kind": "inconclusive",
                "condition": "fail",
                "negation": "fail",
                "detail": "oracle gave the same answer to the predicate and its negation",
                "raw": "Action: Terminate with status fail because test 14 is not visible.\n",
                "negated_raw": "Action: Terminate with status fail.\n",
            }
        ]
    }
    oracle = _oracle_summary(result)
    assert oracle is not None
    assert oracle["condition"] == "fail"
    assert oracle["negation"] == "fail"
    assert oracle["condition_said"].startswith("Terminate with status fail because")
    assert oracle["negation_said"] == "Terminate with status fail."
    assert "same answer" in oracle["detail"]


# --- resolving a case from a file, flags, or both -------------------------


def _args(**overrides: Any) -> argparse.Namespace:
    """The namespace `main()` would build, with every flag unset."""
    defaults: dict[str, Any] = {
        "case": None,
        "instruction": None,
        "success": None,
        "max_actions": None,
        "max_waits": None,
        "wall_clock_s": None,
        "verify_timeout_s": None,
        "no_warmup": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _write_case(tmp_path: Path, **overrides: Any) -> Path:
    payload: dict[str, Any] = {
        "id": "audio-metadata",
        "name": "Audio metadata",
        "instruction": "Open the Test 14 chat",
        "success": "the audio message is labelled Verified Track",
        "max_actions": 70,
        "max_waits": 20,
        "wall_clock_s": 2400,
    }
    payload.update(overrides)
    path = tmp_path / "case.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_a_case_file_supplies_every_argument(tmp_path: Path) -> None:
    case = _resolve_case(_args(case=_write_case(tmp_path)))
    assert case.instruction == "Open the Test 14 chat"
    assert case.max_actions == 70
    assert case.max_waits == 20
    assert case.wall_clock_s == 2400


def test_an_explicit_flag_beats_the_stored_case(tmp_path: Path) -> None:
    """Re-running a saved case with one budget raised must not edit the file."""
    case = _resolve_case(_args(case=_write_case(tmp_path), max_actions=5))
    assert case.max_actions == 5
    assert case.max_waits == 20


def test_a_flag_matching_the_default_still_beats_the_case(tmp_path: Path) -> None:
    """Why every budget parses to None: 20 given is not 20 unset."""
    case = _resolve_case(_args(case=_write_case(tmp_path), max_actions=20))
    assert case.max_actions == 20


def test_without_a_case_the_usual_defaults_apply() -> None:
    case = _resolve_case(_args(instruction="Explore Settings"))
    assert case.max_actions == 20
    assert case.max_waits == 12
    assert case.wall_clock_s == 240
    assert case.success is None


def test_one_of_instruction_or_case_is_required() -> None:
    with pytest.raises(ValueError, match="one of --instruction or --case"):
        _resolve_case(_args())


def test_no_warmup_turns_warmup_off_and_never_back_on(tmp_path: Path) -> None:
    assert _resolve_case(_args(case=_write_case(tmp_path))).warmup is True
    assert _resolve_case(_args(case=_write_case(tmp_path), no_warmup=True)).warmup is False
    stored_off = _write_case(tmp_path, warmup=False)
    assert _resolve_case(_args(case=stored_off)).warmup is False


def test_a_case_the_runner_could_not_honour_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_actions must be at least 1"):
        _resolve_case(_args(case=_write_case(tmp_path, max_actions=0)))
