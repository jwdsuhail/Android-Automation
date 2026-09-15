"""How a run result becomes an exit code and a JSON footer."""

from __future__ import annotations

from typing import Any

from android_runner.cli import _exit_code, _oracle_summary



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
