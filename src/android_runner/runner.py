"""The single autonomous loop. Verification is optional but never implied."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from android_runner import qwen_vl
from android_runner.client import Completion, truncation_note
from android_runner.config import Settings
from android_runner.signals import action_key, detect_stuck, screen_change
from android_runner.verification import Check, verify


class DeviceLike(Protocol):
    def screenshot(self, path: Path) -> tuple[bytes, int, int]: ...
    def execute(self, action: dict[str, Any]) -> None: ...


class ClientLike(Protocol):
    def complete(
        self, messages: list[dict[str, Any]], *, timeout_s: float | None = None
    ) -> Completion: ...


@dataclass(frozen=True)
class Budget:
    max_actions: int = 20
    max_waits: int = 12
    wall_clock_s: float = 240.0
    verify_timeout_s: float = 8.0


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run(
    instruction: str,
    *,
    success: str | None,
    device: DeviceLike,
    client: ClientLike,
    settings: Settings,
    out_dir: Path,
    budget: Budget,
    on_turn: Callable[[dict[str, Any]], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run until verified, bounded failure, or an unverified actor claim."""
    out_dir.mkdir(parents=True, exist_ok=True)
    started = clock()
    deadline = started + budget.wall_clock_s
    turns: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    history: list[tuple[bytes, str]] = []
    keys: list[str] = []
    actions = 0
    waits = 0
    note: str | None = None
    emit = on_turn or (lambda _turn: None)

    def remaining() -> float:
        return max(0.0, deadline - clock())

    def finish(status: str, detail: str) -> dict[str, Any]:
        result = {
            "status": status,
            "verified": status == "verified",
            "instruction": instruction,
            "success": success,
            "detail": detail,
            "actions": actions,
            "waits": waits,
            "elapsed_s": round(clock() - started, 2),
            "turns": turns,
            "checks": checks,
        }
        _write_json(out_dir / "run.json", result)
        return result

    def capture(name: str) -> tuple[bytes, int, int]:
        path = out_dir / name
        png, width, height = device.screenshot(path)
        if not path.exists():
            path.write_bytes(png)
        return png, width, height

    def check(png: bytes) -> Check:
        # The verifier makes two calls. Splitting the remaining run budget keeps
        # the pair inside the one wall-clock ceiling.
        per_call = min(budget.verify_timeout_s, remaining() / 2)
        if per_call <= 0:
            outcome = Check(None, "run wall clock exhausted", "")
        else:
            outcome = verify(
                client,  # type: ignore[arg-type] - protocol-compatible test clients
                png,
                success or "",
                settings.model_history_n,
                per_call,
            )
        checks.append(outcome.as_dict())
        return outcome

    try:
        current_png, width, height = capture("entry.png")
    except Exception as exc:  # noqa: BLE001 - failures become artifacts
        return finish("device_error", f"{type(exc).__name__}: {exc}")

    if success:
        initial = check(current_png)
        if initial.holds is True:
            return finish("verified", "success condition held before any action")
        if remaining() <= 0:
            return finish("timed_out", "wall clock exhausted during entry check")

    index = 0
    while actions < budget.max_actions and waits < budget.max_waits:
        if remaining() <= 0:
            return finish("timed_out", "wall clock exhausted")

        screen_path = out_dir / f"turn_{index:03d}.png"
        screen_path.write_bytes(current_png)
        messages = qwen_vl.build_messages(
            instruction,
            current_png,
            history,
            settings.model_history_n,
            note,
            thinking=settings.model_thinking,
            reflection=settings.model_reflection,
        )
        completion = client.complete(messages, timeout_s=remaining())
        raw_path = out_dir / f"turn_{index:03d}.raw.txt"
        raw_path.write_text(completion.raw, encoding="utf-8")
        record: dict[str, Any] = {
            "index": index,
            "screenshot": screen_path.name,
            "raw": raw_path.name,
            "latency_ms": round(completion.latency_ms, 2),
            "usage": completion.usage,
            "finish_reason": completion.finish_reason,
            "reasoning_chars": completion.reasoning_chars,
        }
        if completion.error:
            record["error"] = completion.error
            turns.append(record)
            _write_json(out_dir / f"turn_{index:03d}.json", record)
            emit(record)
            return finish("model_error", completion.error)

        try:
            parsed = qwen_vl.parse(completion.raw, completion.reasoning)
            pixels = qwen_vl.to_pixels(parsed.arguments, width, height)
        except ValueError as exc:
            record["error"] = f"{exc}{truncation_note(completion)}"
            turns.append(record)
            _write_json(out_dir / f"turn_{index:03d}.json", record)
            emit(record)
            return finish("parse_error", record["error"])

        record.update(
            {
                "narration": parsed.narration,
                "thinking": parsed.thinking,
                "check": parsed.check,
                "expectation": parsed.expectation,
                "action": parsed.action,
                "arguments": parsed.arguments,
                "pixels": pixels,
            }
        )

        if parsed.action == "terminate":
            record["actor_status"] = str(parsed.arguments.get("status", "success"))
            turns.append(record)
            _write_json(out_dir / f"turn_{index:03d}.json", record)
            emit(record)

            if not success:
                status = (
                    "actor_gave_up"
                    if record["actor_status"].lower() == "fail"
                    else "actor_claimed_success"
                )
                return finish(status, "no independent success condition was supplied")

            try:
                current_png, width, height = capture(f"verify_{index:03d}.png")
            except Exception as exc:  # noqa: BLE001
                return finish("device_error", f"{type(exc).__name__}: {exc}")
            outcome = check(current_png)
            if outcome.holds is True:
                return finish("verified", f"oracle confirmed actor claim at turn {index}")
            if outcome.holds is None:
                return finish("oracle_error", outcome.detail)
            if record["actor_status"].lower() == "fail":
                return finish("actor_gave_up", "actor and oracle agreed success was absent")

            # A false success claim is an attempted action, not a pass.
            actions += 1
            history.append((current_png, qwen_vl.replay(completion.raw)))
            note = "Your success claim was rejected by the independent checker."
            index += 1
            continue

        if parsed.action == "wait":
            waits += 1
        else:
            actions += 1

        try:
            device.execute(pixels)
            if parsed.action != "wait" and settings.step_sleep_s:
                sleep(settings.step_sleep_s)
            after_png, width, height = capture(f"turn_{index:03d}.after.png")
        except Exception as exc:  # noqa: BLE001
            record["error"] = f"{type(exc).__name__}: {exc}"
            turns.append(record)
            _write_json(out_dir / f"turn_{index:03d}.json", record)
            emit(record)
            return finish("device_error", record["error"])

        change = screen_change(current_png, after_png)
        record["screen_delta"] = round(change.mean, 6)
        record["screen_tile_max"] = round(change.tile_max, 6)
        record["moved"] = change.moved
        turns.append(record)
        _write_json(out_dir / f"turn_{index:03d}.json", record)
        emit(record)

        history.append((current_png, qwen_vl.replay(completion.raw)))
        note = (
            "A localized screen change was detected after your previous action. "
            "That is not proof the action succeeded; check it against your previous Expect."
            if change.moved
            else "No localized screen change was detected after your previous action. "
            "Check that against your previous Expect."
        )
        current_png = after_png

        if parsed.action != "wait":
            keys.append(action_key(pixels, width, height))
            stuck = detect_stuck(keys)
            if stuck.stuck:
                if success:
                    outcome = check(current_png)
                    if outcome.holds is True:
                        return finish("verified", "oracle confirmed success despite repetition")
                    if outcome.holds is None:
                        return finish("oracle_error", outcome.detail)
                return finish("stuck", stuck.detail)
        index += 1

    if remaining() <= 0:
        return finish("timed_out", "wall clock exhausted")

    if success:
        outcome = check(current_png)
        if outcome.holds is True:
            return finish("verified", "oracle confirmed success on the final check")
        if outcome.holds is None:
            return finish("oracle_error", outcome.detail)
    return finish("budget_exhausted", "action or wait budget exhausted")
