"""Command-line entry point for the single runner."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from android_runner.client import ModelClient
from android_runner.config import Settings, load_env_file
from android_runner.device import AdbDevice
from android_runner.runner import Budget, run


def _point(value: object) -> str | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{value[0]},{value[1]}"
    return None


def _target(turn: dict[str, object]) -> str:
    """The part of the reply the narration leaves out: what it aimed at.

    The sentence says "the plus button"; only the coordinate says *where* the
    model thought that was. Grid and pixels are both shown because a tap that
    lands wrong is either a misread screen (grid) or a bad mapping (pixels),
    and the pair tells them apart without opening turn_NNN.json.
    """
    grid = turn.get("arguments")
    pixels = turn.get("pixels")
    if not isinstance(grid, dict) or not isinstance(pixels, dict):
        return ""

    action = grid.get("action")
    if action == "type":
        return f' "{grid.get("text", "")}"'
    if action == "system_button":
        return f" {grid.get('button', '?')}"
    if action == "wait":
        return f" {grid.get('time', '?')}s"
    if action == "terminate":
        return f" {grid.get('status', '?')}"

    for start, end in (("coordinate", "coordinate2"), ("start_coordinate", "end_coordinate")):
        first = _point(grid.get(start))
        if first is None:
            continue
        second = _point(grid.get(end))
        span = first if second is None else f"{first} -> {second}"
        detail = f" {span} of 1000, px {_point(pixels.get(start))}"
        if second is not None:
            detail += f" -> {_point(pixels.get(end))}"
        if action == "long_press":
            detail += f", {grid.get('duration_ms', '?')}ms"
        return detail
    return ""


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
    print(f"     -> {action}{_target(turn)}{suffix}", file=sys.stderr, flush=True)


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
    parser.add_argument("--verify-timeout-s", type=float, default=8)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--no-env-file", action="store_true")
    args = parser.parse_args(argv)

    if args.max_actions < 1 or args.max_waits < 1:
        parser.error("--max-actions and --max-waits must be at least 1")
    if args.wall_clock_s <= 0 or args.verify_timeout_s <= 0:
        parser.error("--wall-clock-s and --verify-timeout-s must be greater than 0")

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
        ),
        on_turn=_print_turn,
    )

    summary = {
        key: result[key]
        for key in (
            "status",
            "verified",
            "detail",
            "actions",
            "waits",
            "elapsed_s",
        )
    }
    print(json.dumps(summary, indent=2))
    print(f"wrote {out_dir}")

    # Exit 0 is reserved for independently verified success.
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
