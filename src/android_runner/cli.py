"""Command-line entry point for the single runner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from android_runner.cases import Case
from android_runner.client import ModelClient
from android_runner.config import Settings, load_env_file
from android_runner.device import AdbDevice
from android_runner.format import action_said, turn_line
from android_runner.runner import (
    DEFAULT_MAX_ACTIONS,
    DEFAULT_MAX_WAITS,
    UNMEASURED,
    Budget,
    new_run_id,
    run,
    write_json,
)


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
    print(f"     -> {turn_line(turn)}", file=sys.stderr, flush=True)
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
    # Neither answer says anything about the agent, so neither may exit like a
    # failed task: one is a harness that broke, the other a judge that would
    # not answer. The set is `runner`'s, so it cannot drift from `error_class`.
    return 2 if result.get("error_class") in UNMEASURED else 1


def _resolve_case(args: argparse.Namespace) -> Case:
    """The case this invocation runs, from a file, flags, or both.

    A flag given on the command line always beats the stored case, so a saved
    case can be re-run with one budget raised without editing the file. The
    result is validated as a `Case` whether or not a file was involved, so the
    terminal and the console enforce one rule.
    """
    stored: Case | None = None
    if args.case is not None:
        payload = json.loads(Path(args.case).read_text(encoding="utf-8"))
        stored = Case.from_dict(payload)

    if args.instruction is None and stored is None:
        raise ValueError("one of --instruction or --case is required")

    def pick(flag: object, saved: object, fallback: object) -> object:
        if flag is not None:
            return flag
        return saved if stored is not None else fallback

    case = Case(
        id=stored.id if stored else "ad-hoc",
        name=stored.name if stored else "ad-hoc",
        instruction=args.instruction or (stored.instruction if stored else ""),
        success=args.success if args.success is not None else (stored.success if stored else None),
        max_actions=int(
            pick(args.max_actions, stored.max_actions if stored else None, DEFAULT_MAX_ACTIONS)
        ),
        max_waits=int(
            pick(args.max_waits, stored.max_waits if stored else None, DEFAULT_MAX_WAITS)
        ),
        verify_timeout_s=(
            args.verify_timeout_s
            if args.verify_timeout_s is not None
            else (stored.verify_timeout_s if stored else None)
        ),
        # `--no-warmup` can only ever turn warm-up off, never back on, so a case
        # that stored `warmup: false` stays off without the flag.
        warmup=(stored.warmup if stored else True) and not args.no_warmup,
        # Carried so the snapshot in the run directory says which version of
        # the case was run. Empty for an ad-hoc invocation, which is the truth:
        # that case was never saved.
        created_at=stored.created_at if stored else "",
        updated_at=stored.updated_at if stored else "",
    )
    case.validate()
    return case


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one bounded Android GUI agent with optional verification."
    )
    parser.add_argument(
        "--case",
        type=Path,
        default=None,
        help="A stored case file. Supplies every argument below; any flag given "
        "explicitly still wins.",
    )
    parser.add_argument("--instruction", default=None)
    parser.add_argument(
        "--success",
        default=None,
        help="Visible condition required for a verified pass. Omit for exploration.",
    )
    # Every budget defaults to None rather than to its value, so that "not
    # given" can be told from "given the same as the default". A case can only
    # supply what the command line did not.
    parser.add_argument("--max-actions", type=int, default=None)
    parser.add_argument("--max-waits", type=int, default=None)
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

    try:
        case = _resolve_case(args)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    if not args.no_env_file:
        loaded = load_env_file(args.env_file)
        if loaded:
            print(f"loaded {len(loaded)} variables from {args.env_file}", flush=True)

    try:
        settings = Settings.from_env()
    except (TypeError, ValueError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    if settings.app_package:
        print(f"closing {settings.app_package} before the run", flush=True)

    stamp = new_run_id()
    out_dir = args.out or Path("runs") / stamp

    # The case as it was when run, written before the first turn so that a run
    # in progress already says what it is running. Later edits to the stored
    # case must not rewrite the history of what this run actually executed.
    if args.case is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        write_json(out_dir / "case.json", case.as_dict())

    result = run(
        case.instruction,
        success=case.success,
        device=AdbDevice(settings),
        client=ModelClient(settings),
        settings=settings,
        out_dir=out_dir,
        budget=Budget(
            max_actions=case.max_actions,
            max_waits=case.max_waits,
            verify_timeout_s=case.verify_timeout_s,
            warmup=case.warmup,
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
