"""The single autonomous loop. Verification is optional but never implied."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from android_runner import hold, overlay, qwen_vl
from android_runner.client import Completion, truncation_note
from android_runner.config import Settings
from android_runner.signals import (
    Settled,
    action_key,
    detect_stuck,
    screen_change,
    wait_until_settled,
)
from android_runner.verification import INFRASTRUCTURE, Check, CheckPhase, verify

# A run either measures the agent or it does not. A dead device, a dead server
# and a judge that never answered say nothing about whether the task was done,
# so the caller has to be able to tell them from a task the agent failed.
_INFRASTRUCTURE_STATUSES = frozenset({"device_error", "model_error", "oracle_error"})

# A third thing, and it used to be filed under the first. Here the harness
# worked and the agent is not what failed: a reply arrived from the judge and
# was unusable. Calling that "infrastructure" reported a waffling grader as a
# broken server, which is the opposite of the split this exists to draw.
_ORACLE_STATUSES = frozenset({"oracle_inconclusive"})

# The three answers `error_class` can give, named once. `INFRASTRUCTURE` comes
# from `verification` rather than being spelled again here, and the console
# reads all three from this module rather than re-declaring the strings: a
# vocabulary that decides how a run is filed should have one definition.
ORACLE = "oracle"
AGENT = "agent"

# A required checkpoint the run never passed through, with a real "no" from
# the judge behind it. It falls through `error_class` to `AGENT` because that
# is what it is: the harness worked, the judge answered, and the agent did not
# go through the state the case names. A rung that was never *answered* is a
# different thing and is filed as one of the two oracle statuses instead.
CHECKPOINTS_INCOMPLETE = "checkpoints_incomplete"

# Neither says anything about the agent. A caller deciding whether a run
# measured anything wants this set, not the individual strings.
UNMEASURED = frozenset({INFRASTRUCTURE, ORACLE})


def error_class(status: str) -> str | None:
    """"infrastructure", "oracle", "agent", or None for a verified pass."""
    if status == "verified":
        return None
    if status in _INFRASTRUCTURE_STATUSES:
        return INFRASTRUCTURE
    if status in _ORACLE_STATUSES:
        return ORACLE
    return AGENT


class DeviceLike(Protocol):
    def screenshot(self, path: Path) -> tuple[bytes, int, int]: ...
    def execute(self, action: dict[str, Any]) -> None: ...
    def long_press_floor_ms(self) -> int: ...
    def close_app(self, package: str) -> None: ...


class CheckpointLike(Protocol):
    """What the loop needs from a rung of the case.

    Structural rather than imported, for the same reason `DeviceLike` is:
    `cases.py` imports this module for the budget defaults, so importing
    `cases.Checkpoint` back would be a cycle - and the loop has no business
    knowing that a saved case is where these usually come from.
    """

    id: str
    condition: str
    after: str | None
    required: bool
    max_polls: int


class ClientLike(Protocol):
    def complete(
        self, messages: list[dict[str, Any]], *, timeout_s: float | None = None
    ) -> Completion: ...


# A run directory is named for the moment it started: UTC, to the second. The
# terminal and the console both create runs and the console parses the name
# back into a time, so the format is written down once for all three.
RUN_ID_STAMP = "%Y%m%dT%H%M%SZ"


def new_run_id() -> str:
    """The id of a fresh run directory."""
    return datetime.now(timezone.utc).strftime(RUN_ID_STAMP)


# The bounds a run is given when nobody names one. `Case` defaults to these too
# rather than repeating the numbers, so raising a budget is a one-line change
# and the terminal and the console cannot start from different ceilings.
DEFAULT_MAX_ACTIONS = 20
DEFAULT_MAX_WAITS = 12
# How many times one checkpoint may be put to the oracle. Three is enough for
# the shape this is for - a trigger phrase that recurs, like "tap Logout"
# matching both the button and the confirm dialog - and low enough that a
# pattern matching every turn cannot spend the run on one rung.
DEFAULT_CHECKPOINT_POLLS = 3


@dataclass(frozen=True)
class Budget:
    max_actions: int = DEFAULT_MAX_ACTIONS
    max_waits: int = DEFAULT_MAX_WAITS
    # None gives the oracle the same ceiling as the actor. Starving the grader
    # is what made a slow server indistinguishable from a failed task.
    verify_timeout_s: float | None = None
    # Spent on a failed transport only, never on a verdict.
    verify_attempts: int = 2
    # One throwaway call before the run, so the first real call is not the one
    # paying for weight load and CUDA graph capture. Off by default to keep the
    # library loop a pure function of its replies; the CLI turns it on.
    warmup: bool = False
    # A whole-run ceiling on checkpoint evaluations, above each rung's own
    # `max_polls`. None means the per-rung caps are the only bound, which they
    # already are: the worst case is their sum. It exists for the case with
    # many rungs where that sum is more oracle time than the run is worth.
    max_checkpoint_polls: int | None = None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Every JSON artifact this project writes, spelled one way.

    Indented and newline-terminated because these files are read by people and
    diffed by git as often as they are parsed.
    """
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


@dataclass
class _Rung:
    """One checkpoint, as the run is finding out about it.

    Separate from the `CheckpointLike` it points at because that is the case -
    the same object across every run of it - while this is what happened this
    time.
    """

    spec: CheckpointLike
    index: int
    # None when the rung named no trigger, and also when the pattern would not
    # compile. The second is recorded rather than raised: a bad pattern is a
    # case that will not fire a rung, not a reason to write no run at all.
    pattern: re.Pattern[str] | None = None
    trigger_error: str | None = None
    # The pattern matched something the actor said, whether or not a poll was
    # then spent. `triggered: false` beside `met: false` is the difference
    # between a rung that failed and a rung nobody looked at.
    triggered: bool = False
    met: bool = False
    polls: int = 0
    # Any usable verdict at all, including "no". This is what separates an
    # agent that did not go through the state from a judge that never said.
    answered: bool = False
    # The kind of the last non-answer, so an unmet rung can say whether the
    # transport died or the judge was unreadable.
    kind: str | None = None
    turn: int | None = None
    screenshot: str | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.spec.id,
            "condition": self.spec.condition,
            "after": self.spec.after,
            "required": self.spec.required,
            "met": self.met,
            "triggered": self.triggered,
            "polls": self.polls,
            "turn": self.turn,
            "screenshot": self.screenshot,
            "detail": self.detail,
        }
        if self.trigger_error:
            payload["trigger_error"] = self.trigger_error
        return payload


def _rungs(checkpoints: Sequence[CheckpointLike]) -> list[_Rung]:
    """Compile each trigger once, at the start, where a failure is visible."""
    built: list[_Rung] = []
    for index, spec in enumerate(checkpoints):
        rung = _Rung(spec=spec, index=index)
        if spec.after:
            try:
                rung.pattern = re.compile(spec.after, re.IGNORECASE)
            except re.error as exc:
                rung.trigger_error = f"{spec.after!r} is not a valid pattern: {exc}"
        built.append(rung)
    return built


def run(
    instruction: str,
    *,
    success: str | None,
    device: DeviceLike,
    client: ClientLike,
    settings: Settings,
    out_dir: Path,
    budget: Budget,
    checkpoints: Sequence[CheckpointLike] = (),
    on_turn: Callable[[dict[str, Any]], None] | None = None,
    on_check: Callable[[dict[str, Any]], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run until verified, bounded failure, or an unverified actor claim."""
    out_dir.mkdir(parents=True, exist_ok=True)
    started = clock()
    turns: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    # Checkpoints qualify a success condition - they say how the run reached
    # it, not whether it did - so without one there is nothing for them to
    # qualify and they are not graded. `cases.py` refuses that combination at
    # the door; this is the same rule for a caller who built a run by hand.
    rungs = _rungs(checkpoints) if success else []
    checkpoint_polls = 0
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
    last_settle: Settled | None = None
    current_screenshot = "entry.png"
    current_turn: int | None = None
    emit = on_turn or (lambda _turn: None)
    emit_check = on_check or (lambda _check: None)

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
        # Only when the case named some. A run with no checkpoints writes the
        # same `run.json` it wrote before they existed, key for key.
        if rungs:
            result["checkpoints"] = [rung.as_dict() for rung in rungs]
        write_json(out_dir / "run.json", result)
        return result

    def capture(name: str) -> tuple[bytes, int, int]:
        path = out_dir / name
        png, width, height = device.screenshot(path)
        if not path.exists():
            path.write_bytes(png)
        return png, width, height

    def capture_settled(name: str) -> tuple[bytes, int, int]:
        """Shoot the same file until the screen stops moving.

        Overwriting one filename rather than numbering the frames leaves the
        artifact set exactly as it was; the frame that survives is the one the
        model is shown, which is the one worth keeping. A run with the settle
        loop off records nothing, so `settled: false` never means "off".
        """
        nonlocal last_settle
        size = [0, 0]

        def shoot() -> bytes:
            png, width, height = capture(name)
            size[0], size[1] = width, height
            return png

        outcome = wait_until_settled(
            shoot,
            timeout_s=settings.settle_timeout_s,
            clock=clock,
            sleep=sleep,
        )
        last_settle = outcome if settings.settle_timeout_s > 0 else None
        return outcome.png, size[0], size[1]

    def check(
        png: bytes,
        *,
        screenshot: str,
        phase: CheckPhase,
        turn: int | None = None,
        rung: _Rung | None = None,
    ) -> Check:
        # Every call gets the actor's own ceiling. There is no run-wide total
        # left to divide between the two questions and their retries, and
        # starving the grader is what made a slow server indistinguishable
        # from a failed task.
        outcome = verify(
            client,  # type: ignore[arg-type] - protocol-compatible test clients
            png,
            rung.spec.condition if rung is not None else (success or ""),
            settings.model_history_n,
            budget.verify_timeout_s or settings.model_timeout_s,
            max(1, budget.verify_attempts),
            screenshot=screenshot,
            phase=phase,
            turn=turn,
        )
        if rung is not None:
            # `verify` does not know cases exist, so the rung is attached to
            # the verdict rather than asked for inside it. That keeps the two
            # questions, the pairing and the inconclusive handling identical
            # to every other check in the run.
            outcome = replace(
                outcome, checkpoint_id=rung.spec.id, checkpoint_index=rung.index
            )
        record = outcome.as_dict()
        checks.append(record)
        # `run.json` is written only at finish. Keeping each check beside the
        # turn records lets the directory-backed console stream the existing
        # on_check payload while the run is still live.
        write_json(out_dir / f"check_{len(checks) - 1:03d}.json", record)
        emit_check(record)
        return outcome

    def oracle_status(outcome: Check) -> str:
        """A judge that never answered is not a judge that said no."""
        return (
            "oracle_error" if outcome.kind == INFRASTRUCTURE else "oracle_inconclusive"
        )

    def outranks_verdict(outcome: Check) -> bool:
        """May a non-answer from the oracle replace a verdict the runner has?

        Only when the transport failed, because then nothing was measured at
        all. A judge that answered and was unusable leaves the run exactly as
        measurable as it was a moment earlier - `stuck` is still `stuck` - and
        overwriting it filed a real agent failure as a broken harness. That is
        what happened to the 2026-09-15 20:55 run: detect_stuck had already
        caught three repeated taps and ended the run correctly.
        """
        return outcome.kind == INFRASTRUCTURE

    def noting(detail: str, outcome: Check) -> str:
        """The oracle's non-answer, kept beside the verdict it did not change."""
        return f"{detail}; the oracle was inconclusive: {outcome.detail}"

    def polls_left(rung: _Rung) -> bool:
        """Whether this rung may be put to the oracle once more.

        A non-answer counts. It cost two model calls and it looked at the same
        screen, so not counting it is how one unreadable rung spends a run. It
        does not count *against the agent* - an unanswered rung ends the run on
        an oracle status, not on a failed task - which is the part of the
        `outranks_verdict` reasoning that matters here.
        """
        if rung.polls >= max(1, rung.spec.max_polls):
            return False
        ceiling = budget.max_checkpoint_polls
        return ceiling is None or checkpoint_polls < ceiling

    def evaluate(rung: _Rung, png: bytes, *, screenshot: str, turn: int | None) -> None:
        """Put one rung to the oracle, on the screen a named turn left behind."""
        nonlocal checkpoint_polls
        outcome = check(png, screenshot=screenshot, phase="checkpoint", turn=turn, rung=rung)
        rung.polls += 1
        checkpoint_polls += 1
        if outcome.holds is None:
            rung.kind = outcome.kind
            rung.detail = outcome.detail
            return
        rung.answered = True
        rung.kind = None
        if outcome.holds:
            rung.met = True
            rung.turn = turn
            rung.screenshot = screenshot
            rung.detail = (
                "the oracle confirmed this step"
                + (f" after turn {turn}" if turn is not None else "")
            )
        else:
            rung.detail = "the oracle looked and did not find this step"

    def fires(rung: _Rung, narration: str | None, expectation: str | None) -> bool:
        """Does what the actor said it was doing name this rung?

        Its narration and its Expect line, not a turn number: two runs of the
        same case diverge by turn 1 and never realign, so an index anchors to a
        different screen in each. The self-report decides only *when to look*.
        """
        if rung.pattern is None:
            return False
        said = "\n".join(part for part in (narration, expectation) if part)
        return bool(said) and rung.pattern.search(said) is not None

    def cross(narration: str | None, expectation: str | None, png: bytes) -> None:
        """Evaluate every unmet rung this turn named. Usually none, costing nothing."""
        for rung in rungs:
            if rung.met or not fires(rung, narration, expectation):
                continue
            # Recorded even when there is no poll left to spend: a rung whose
            # trigger fired and whose budget ran out is a different failure
            # from one nothing ever matched.
            rung.triggered = True
            if not polls_left(rung):
                continue
            evaluate(rung, png, screenshot=current_screenshot, turn=current_turn)

    def passed(detail: str, png: bytes, *, screenshot: str, turn: int | None) -> dict[str, Any]:
        """The success condition held. Did the run go through the named steps?

        Any rung whose trigger never fired is asked once here, so that an
        oddly-narrated turn is not the sole reason a step is reported missed.
        A rung that did fire has already been measured and is not asked again.

        The three outcomes below are the same split the rest of the runner
        draws. A rung answered "no" is the agent's failure. A rung nobody could
        get an answer about says nothing about the agent, and filing it as a
        failed task would be the exact mistake `oracle_inconclusive` exists to
        prevent - one run's dead transport reported as an agent that skipped a
        step.
        """
        for rung in rungs:
            if rung.met or rung.triggered or not polls_left(rung):
                continue
            evaluate(rung, png, screenshot=screenshot, turn=turn)
        missed = [rung for rung in rungs if rung.spec.required and not rung.met]
        if not missed:
            return finish("verified", detail)
        named = ", ".join(rung.spec.id for rung in missed)
        unanswered = [rung for rung in missed if not rung.answered]
        if any(rung.kind == INFRASTRUCTURE for rung in unanswered):
            return finish(
                "oracle_error",
                f"{detail}, but no verdict ever arrived for: {named}",
            )
        if unanswered:
            return finish(
                "oracle_inconclusive",
                f"{detail}, but the oracle never gave a usable answer for: {named}",
            )
        return finish(
            CHECKPOINTS_INCOMPLETE,
            f"{detail}, but the run never passed through: {named}",
        )

    # Opt-in, and off unless APP_PACKAGE names something. A run that inherits
    # the last run's half-open dialog is measuring the previous test as much as
    # this one - but force-stopping by default cost more than it bought: every
    # run then began on the launcher, the agent spent turn 0 opening the app,
    # and the next screenshot caught it mid-draw. Closing happens before the
    # entry screenshot, so what is captured is the state the agent really
    # starts in.
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
        current_png, width, height = capture_settled("entry.png")
    except Exception as exc:  # noqa: BLE001 - failures become artifacts
        return finish("device_error", f"{type(exc).__name__}: {exc}")

    # Paid once, deliberately, before anything is measured. Without it the
    # first call of the run absorbs weight load, torch.compile and CUDA graph
    # capture - which is how the run's first real call became an unpaid
    # warm-up that timed out doing the job.
    if budget.warmup:
        warmup_started = clock()
        try:
            warm = client.complete(
                qwen_vl.warmup_messages(current_png),
                timeout_s=settings.model_timeout_s,
            )
            warmup_error = warm.error
        except Exception as exc:  # noqa: BLE001 - a warm-up must not end a run
            warmup_error = f"{type(exc).__name__}: {exc}"
        warmup_ms = round((clock() - warmup_started) * 1000, 2)

    index = 0
    while actions < budget.max_actions and waits < budget.max_waits:
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
        completion = client.complete(messages, timeout_s=settings.model_timeout_s)
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
            write_json(out_dir / f"turn_{index:03d}.json", record)
            emit(record)
            return finish("model_error", completion.error)

        try:
            parsed = qwen_vl.parse(completion.raw, completion.reasoning)
            pixels = qwen_vl.to_pixels(parsed.arguments, width, height)
        except ValueError as exc:
            record["error"] = f"{exc}{truncation_note(completion)}"
            turns.append(record)
            write_json(out_dir / f"turn_{index:03d}.json", record)
            emit(record)
            return finish("parse_error", record["error"])

        # The Check line is the one part of the format the model quietly stops
        # producing: it survives turn 0 and is gone by turn 1 in every run on
        # disk. Replaying Check in the history removes the cause; saying so
        # when it is missing is what makes the next turn put it back instead of
        # hoping imitation holds.
        check_reminder = (
            " You did not write a Check line last turn. Write one now: say"
            " whether the screen matches your previous Expect, before choosing"
            " an action."
            if settings.model_reflection and index > 0 and parsed.check is None
            else ""
        )

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
                write_json(out_dir / f"turn_{index:03d}.json", record)
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
            write_json(out_dir / f"turn_{index:03d}.json", record)
            emit(record)

            if not success:
                status = (
                    "actor_gave_up"
                    if record["actor_status"].lower() == "fail"
                    else "actor_claimed_success"
                )
                return finish(status, "no independent success condition was supplied")

            verify_name = f"verify_{index:03d}.png"
            try:
                # Settled, like every other frame the model is shown. This was
                # the one screenshot a verdict is read from that was taken as a
                # single shot, which made the most consequential check in the
                # run the least defended against a half-drawn screen.
                current_png, width, height = capture_settled(verify_name)
            except Exception as exc:  # noqa: BLE001
                return finish("device_error", f"{type(exc).__name__}: {exc}")
            current_screenshot = verify_name
            current_turn = index
            outcome = check(
                current_png,
                screenshot=current_screenshot,
                phase="actor_claim",
                turn=current_turn,
            )
            if outcome.holds is True:
                return passed(
                    f"oracle confirmed actor claim at turn {index}",
                    current_png,
                    screenshot=current_screenshot,
                    turn=current_turn,
                )
            if outcome.holds is None:
                return finish(oracle_status(outcome), outcome.detail)
            if record["actor_status"].lower() == "fail":
                return finish("actor_gave_up", "actor and oracle agreed success was absent")

            # A false success claim is an attempted action, not a pass.
            actions += 1
            history.append((current_png, qwen_vl.replay(completion.raw)))
            note = (
                "Your success claim was rejected by the independent checker."
                + check_reminder
            )
            index += 1
            continue

        if parsed.action == "wait":
            waits += 1
        else:
            actions += 1

        try:
            device.execute(pixels)
            # The minimum delay that lets the tap register at all. The settle
            # poll below decides how much longer to wait; this is the floor
            # under it, not a guess at the app's draw time.
            if parsed.action != "wait" and settings.step_sleep_s:
                sleep(settings.step_sleep_s)
            after_name = f"turn_{index:03d}.after.png"
            after_png, width, height = capture_settled(after_name)
        except Exception as exc:  # noqa: BLE001
            record["error"] = f"{type(exc).__name__}: {exc}"
            turns.append(record)
            write_json(out_dir / f"turn_{index:03d}.json", record)
            emit(record)
            return finish("device_error", record["error"])

        change = screen_change(current_png, after_png)
        record["screen_delta"] = round(change.mean, 6)
        record["screen_tile_max"] = round(change.tile_max, 6)
        record["moved"] = change.moved
        if last_settle is not None:
            record["settle_ms"] = last_settle.ms
            record["settled"] = last_settle.settled
        turns.append(record)
        write_json(out_dir / f"turn_{index:03d}.json", record)
        emit(record)

        history.append((current_png, qwen_vl.replay(completion.raw)))
        note = (
            "A localized screen change was detected after your previous action. "
            "That is not proof the action succeeded; check it against your previous Expect."
            if change.moved
            else "No localized screen change was detected after your previous action. "
            "Check that against your previous Expect."
        ) + check_reminder
        current_png = after_png
        current_screenshot = after_name
        current_turn = index

        # Before the repetition check, so a rung the last turn crossed is
        # recorded even when that turn is the one that ends the run. A turn
        # matching no trigger costs nothing, which is the whole point: the
        # oracle runs at the steps you named and nowhere else.
        cross(parsed.narration, parsed.expectation, current_png)

        if parsed.action != "wait":
            keys.append(action_key(pixels, width, height))
            stuck = detect_stuck(keys)
            if stuck.stuck:
                if success:
                    outcome = check(
                        current_png,
                        screenshot=current_screenshot,
                        phase="stuck",
                        turn=current_turn,
                    )
                    if outcome.holds is True:
                        return passed(
                            "oracle confirmed success despite repetition",
                            current_png,
                            screenshot=current_screenshot,
                            turn=current_turn,
                        )
                    if outcome.holds is None:
                        if outranks_verdict(outcome):
                            return finish(oracle_status(outcome), outcome.detail)
                        return finish("stuck", noting(stuck.detail, outcome))
                return finish("stuck", stuck.detail)
        index += 1

    exhausted = "action or wait budget exhausted"
    if success:
        outcome = check(
            current_png,
            screenshot=current_screenshot,
            phase="final",
            turn=current_turn,
        )
        if outcome.holds is True:
            return passed(
                "oracle confirmed success on the final check",
                current_png,
                screenshot=current_screenshot,
                turn=current_turn,
            )
        if outcome.holds is None:
            if outranks_verdict(outcome):
                return finish(oracle_status(outcome), outcome.detail)
            return finish("budget_exhausted", noting(exhausted, outcome))
    return finish("budget_exhausted", exhausted)
