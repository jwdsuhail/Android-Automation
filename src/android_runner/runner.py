"""The single autonomous loop. Verification is optional but never implied."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from android_runner import hold, overlay, qwen_vl
from android_runner.client import Completion, truncation_note
from android_runner.config import Settings
from android_runner.signals import action_key, detect_stuck, screen_change
from android_runner.verification import INFRASTRUCTURE, Check, verify

# A run either measures the agent or it does not. A dead device, a dead server
# and a judge that never answered say nothing about whether the task was done,
# so the caller has to be able to tell them from a task the agent failed.
_INFRASTRUCTURE_STATUSES = frozenset(
    {"device_error", "model_error", "oracle_error", "oracle_inconclusive"}
)


def error_class(status: str) -> str | None:
    """"infrastructure", "agent", or None for a verified pass."""
    if status == "verified":
        return None
    return "infrastructure" if status in _INFRASTRUCTURE_STATUSES else "agent"


class DeviceLike(Protocol):
    def screenshot(self, path: Path) -> tuple[bytes, int, int]: ...
    def execute(self, action: dict[str, Any]) -> None: ...
    def long_press_floor_ms(self) -> int: ...
    def close_app(self, package: str) -> None: ...


class ClientLike(Protocol):
    def complete(
        self, messages: list[dict[str, Any]], *, timeout_s: float | None = None
    ) -> Completion: ...


@dataclass(frozen=True)
class Budget:
    max_actions: int = 20
    max_waits: int = 12
    wall_clock_s: float = 240.0
    # None gives the oracle the same ceiling as the actor. Starving the grader
    # is what made a slow server indistinguishable from a failed task.
    verify_timeout_s: float | None = None
    # Spent on a failed transport only, never on a verdict.
    verify_attempts: int = 2
    # One throwaway call before the run, so the first real call is not the one
    # paying for weight load and CUDA graph capture. Off by default to keep the
    # library loop a pure function of its replies; the CLI turns it on.
    warmup: bool = False


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
    on_check: Callable[[dict[str, Any]], None] | None = None,
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
    # The instruction does not change between turns, so it is read once. The
    # device's long-press threshold does not either, and asking for it costs
    # one shell call against thirty turns that would each ask the same thing.
    instruction_ms, instruction_notes = hold.from_instruction(instruction)
    hold_floor_ms = device.long_press_floor_ms()
    hold_ceiling_ms = hold.ceiling_ms(settings.adb_timeout_s)
    warmup_ms: float | None = None
    warmup_error: str | None = None
    closed_app: str | None = None
    emit = on_turn or (lambda _turn: None)
    emit_check = on_check or (lambda _check: None)

    def remaining() -> float:
        return max(0.0, deadline - clock())

    def finish(status: str, detail: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": status,
            "verified": status == "verified",
            "error_class": error_class(status),
            "instruction": instruction,
            "success": success,
            "detail": detail,
            "actions": actions,
            "waits": waits,
            "elapsed_s": round(clock() - started, 2),
            "turns": turns,
            "checks": checks,
        }
        if warmup_ms is not None:
            result["warmup_ms"] = warmup_ms
        if warmup_error:
            result["warmup_error"] = warmup_error
        if closed_app:
            result["closed_app"] = closed_app
        _write_json(out_dir / "run.json", result)
        return result

    def capture(name: str) -> tuple[bytes, int, int]:
        path = out_dir / name
        png, width, height = device.screenshot(path)
        if not path.exists():
            path.write_bytes(png)
        return png, width, height

    def check(png: bytes) -> Check:
        # The verifier asks two questions and may retry each one. Dividing by
        # the worst case keeps the whole pair inside the one wall-clock
        # ceiling; capping it at the actor's own ceiling stops the grader from
        # being the only component on a short timeout.
        attempts = max(1, budget.verify_attempts)
        ceiling = budget.verify_timeout_s or settings.model_timeout_s
        per_call = min(ceiling, remaining() / (2 * attempts))
        if per_call <= 0:
            outcome = Check(
                None, "run wall clock exhausted", "", kind=INFRASTRUCTURE
            )
        else:
            outcome = verify(
                client,  # type: ignore[arg-type] - protocol-compatible test clients
                png,
                success or "",
                settings.model_history_n,
                per_call,
                attempts,
            )
        record = outcome.as_dict()
        checks.append(record)
        emit_check(record)
        return outcome

    def oracle_status(outcome: Check) -> str:
        """A judge that never answered is not a judge that said no."""
        return (
            "oracle_error" if outcome.kind == INFRASTRUCTURE else "oracle_inconclusive"
        )

    # Every case starts from a cold app. A run that inherits the last run's
    # half-open dialog is measuring the previous test as much as this one, and
    # that is the failure that reads as flakiness rather than as leftover
    # state. Closing happens before the entry screenshot, so what is captured
    # and what the oracle checks first is the state the agent really starts in.
    if settings.app_package:
        try:
            device.close_app(settings.app_package)
        except Exception as exc:  # noqa: BLE001 - failures become artifacts
            return finish(
                "device_error",
                f"could not close {settings.app_package}: {type(exc).__name__}: {exc}",
            )
        closed_app = settings.app_package
        # Force-stop drops the app's window, and the launcher takes a moment to
        # draw. Without this the entry screenshot can catch that animation, and
        # the first thing the agent is shown is a half-faded app it was told
        # was closed.
        if settings.step_sleep_s:
            sleep(settings.step_sleep_s)

    try:
        current_png, width, height = capture("entry.png")
    except Exception as exc:  # noqa: BLE001 - failures become artifacts
        return finish("device_error", f"{type(exc).__name__}: {exc}")

    # Paid once, deliberately, before anything is measured. Without it the
    # first call of the run absorbs weight load, torch.compile and CUDA graph
    # capture - which is how the entry check became an unpaid warm-up that
    # timed out doing the job.
    if budget.warmup:
        warmup_started = clock()
        try:
            warm = client.complete(
                qwen_vl.warmup_messages(current_png),
                timeout_s=min(settings.model_timeout_s, remaining()),
            )
            warmup_error = warm.error
        except Exception as exc:  # noqa: BLE001 - a warm-up must not end a run
            warmup_error = f"{type(exc).__name__}: {exc}"
        warmup_ms = round((clock() - warmup_started) * 1000, 2)

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

        if parsed.action == "long_press":
            # `pixels` is the dict already stored on the record above, so
            # overwriting the duration here reaches both the device and the
            # artifact. Resolving before execute is what makes the record a
            # statement about what happened rather than about what was asked.
            try:
                model_ms, model_notes = hold.from_arguments(parsed.arguments)
            except ValueError as exc:
                record["error"] = f"{exc}{truncation_note(completion)}"
                turns.append(record)
                _write_json(out_dir / f"turn_{index:03d}.json", record)
                emit(record)
                return finish("parse_error", record["error"])
            resolved = hold.resolve(
                instruction_ms,
                model_ms,
                settings.long_press_ms,
                hold_floor_ms,
                hold_ceiling_ms,
                notes=instruction_notes + model_notes,
            )
            pixels["duration_ms"] = resolved.ms
            record["hold"] = resolved.as_dict()

        # Marked on the screen the model was looking at, not the one after the
        # action, so the picture shows what it aimed at rather than where that
        # left the app.
        try:
            marked = overlay.mark(current_png, pixels)
        except Exception as exc:  # noqa: BLE001 - a diagnostic must not end a run
            record["marked_error"] = f"{type(exc).__name__}: {exc}"
        else:
            if marked is not None:
                marked_path = out_dir / f"turn_{index:03d}.marked.png"
                marked_path.write_bytes(marked)
                record["marked"] = marked_path.name

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
                return finish(oracle_status(outcome), outcome.detail)
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
                        return finish(oracle_status(outcome), outcome.detail)
                return finish("stuck", stuck.detail)
        index += 1

    if remaining() <= 0:
        return finish("timed_out", "wall clock exhausted")

    if success:
        outcome = check(current_png)
        if outcome.holds is True:
            return finish("verified", "oracle confirmed success on the final check")
        if outcome.holds is None:
            return finish(oracle_status(outcome), outcome.detail)
    return finish("budget_exhausted", "action or wait budget exhausted")
