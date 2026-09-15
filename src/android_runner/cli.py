"""Command-line entry point for the single runner."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from android_runner.client import ModelClient
from android_runner.config import Settings, load_env_file
from android_runner.device import AdbDevice
from android_runner.format import action_said, target
from android_runner.runner import Budget, run


def _print_turn(turn: dict[str, object]) -> None:
    index = turn.get("index", "?")
    narration = turn.get("narration")
    if narration:
        print(f"[{index}] {narration}", file=sys.stderr, flush=True)
    check = turn.get("check")
    if check:
        print(f"     check: {check}", file=sys.stderr, flush=True)
    expectation = turn.get("expectation")
    if expectation:
        print(f"     expect: {expectation}", file=sys.stderr, flush=True)
    action = turn.get("action", "?")
    moved = turn.get("moved")
    suffix = "" if moved is None else (" moved" if moved else " no-change")
    print(f"     -> {action}{target(turn)}{suffix}", file=sys.stderr, flush=True)
    held = turn.get("hold")
    if isinstance(held, dict):
        for hold_note in held.get("notes") or ():
            print(f"     hold: {hold_note}", file=sys.stderr, flush=True)


def _print_check(check: dict[str, object]) -> None:
    """Show the judge's two answers, not only that they failed to pair."""
    holds = check.get("holds")
    if holds is True:
        label = "pass"
    elif holds is False:
        label = "fail"
    else:
        label = str(check.get("kind") or "no-verdict")
    print(f"     oracle: {label}", file=sys.stderr, flush=True)
    condition = check.get("condition")
    if condition:
        said = action_said(check.get("raw"))
        suffix = f" — {said}" if said else ""
        print(f"     condition: {condition}{suffix}", file=sys.stderr, flush=True)
    negation = check.get("negation")
    if negation:
        said = action_said(check.get("negated_raw"))
        suffix = f" — {said}" if said else ""
        print(f"     negation: {negation}{suffix}", file=sys.stderr, flush=True)
    detail = check.get("detail")
    if holds is None and detail:
        print(f"     reason: {detail}", file=sys.stderr, flush=True)


def _oracle_summary(result: dict[str, Any]) -> dict[str, Any] | None:
    """The last check, compacted for the JSON footer."""
    checks = result.get("checks")
    if not isinstance(checks, list) or not checks:
        return None
    last = checks[-1]
    if not isinstance(last, dict):
        return None
    payload = {
        "holds": last.get("holds"),
        "kind": last.get("kind"),
        "condition": last.get("condition"),
        "negation": last.get("negation"),
        "detail": last.get("detail"),
        "condition_said": action_said(last.get("raw")),
        "negation_said": action_said(last.get("negated_raw")),
    }
    return payload


def _exit_code(result: dict[str, Any]) -> int:
    """0 verified, 1 the agent did not get there, 2 nothing was measured.

    An infrastructure failure is not a failed task. The run carries no signal
    about the agent, so it must not read from the shell like one that does.
    """
    if result.get("verified"):
        return 0
    return 2 if result.get("error_class") == "infrastructure" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one bounded Android GUI agent with optional verification."
    )
    parser.add_argument("--instruction", required=True)
    parser.add_argument(
        "--success",
        default=None,
        help="Visible condition required for a verified pass. Omit for exploration.",
    )
    parser.add_argument("--max-actions", type=int, default=20)
    parser.add_argument("--max-waits", type=int, default=12)
    parser.add_argument("--wall-clock-s", type=float, default=240)
    parser.add_argument(
        "--verify-timeout-s",
        type=float,
        default=None,
        help="Per-oracle-call ceiling. Defaults to MODEL_TIMEOUT_S, the same "
        "ceiling the actor gets.",
    )
    parser.add_argument(
        "--no-warmup",
        action="store_true",
        help="Skip the throwaway first call. The first real call then pays for "
        "model load and graph capture itself.",
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--no-env-file", action="store_true")
    args = parser.parse_args(argv)

    if args.max_actions < 1 or args.max_waits < 1:
        parser.error("--max-actions and --max-waits must be at least 1")
    if args.wall_clock_s <= 0:
        parser.error("--wall-clock-s must be greater than 0")
    if args.verify_timeout_s is not None and args.verify_timeout_s <= 0:
        parser.error("--verify-timeout-s must be greater than 0")

    if not args.no_env_file:
        loaded = load_env_file(args.env_file)
        if loaded:
            print(f"loaded {len(loaded)} variables from {args.env_file}", flush=True)

    try:
        settings = Settings.from_env()
    except (TypeError, ValueError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out or Path("runs") / stamp
    result = run(
        args.instruction,
        success=args.success,
        device=AdbDevice(settings),
        client=ModelClient(settings),
        settings=settings,
        out_dir=out_dir,
        budget=Budget(
            max_actions=args.max_actions,
            max_waits=args.max_waits,
            wall_clock_s=args.wall_clock_s,
            verify_timeout_s=args.verify_timeout_s,
            warmup=not args.no_warmup,
        ),
        on_turn=_print_turn,
        on_check=_print_check,
    )

    summary = {
        key: result[key]
        for key in (
            "status",
            "verified",
            "error_class",
            "detail",
            "actions",
            "waits",
            "elapsed_s",
        )
    }
    oracle = _oracle_summary(result)
    if oracle is not None:
        summary["oracle"] = oracle
    print(json.dumps(summary, indent=2))
    print(f"wrote {out_dir}")

    return _exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
