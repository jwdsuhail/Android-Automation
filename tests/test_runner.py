from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from android_runner.client import Completion
from android_runner.cli import _print_turn
from android_runner.qwen_vl import parse, to_pixels
from android_runner.runner import Budget, error_class, run
from settings import SETTINGS


def png(value: int) -> bytes:
    image = Image.new("L", (20, 20), color=value)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def reply(action: dict[str, Any]) -> str:
    return (
        "Action: test action\n"
        "<tool_call>"
        + json.dumps({"name": "mobile_use", "arguments": action})
        + "</tool_call>"
    )


class FakeDevice:
    def __init__(self) -> None:
        self.changed = False
        self.actions: list[dict[str, Any]] = []
        self.closed: list[str] = []

    def screenshot(self, path: Path) -> tuple[bytes, int, int]:
        data = png(255 if self.changed else 0)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data, 100, 200

    def execute(self, action: dict[str, Any]) -> None:
        self.actions.append(action)
        if action["action"] != "wait":
            self.changed = True

    def long_press_floor_ms(self) -> int:
        return 400  # what the emulator reports on SDK 35

    def close_app(self, package: str) -> None:
        self.closed.append(package)


class FakeClient:
    def __init__(
        self,
        actor_replies: list[str],
        oracle_holds: Callable[[], bool] = lambda: False,
        finish_reason: str = "stop",
        negate: bool = True,
        oracle_error: str | None = None,
    ) -> None:
        self.actor_replies = iter(actor_replies)
        self.oracle_holds = oracle_holds
        self.finish_reason = finish_reason
        # False makes the judge answer the predicate and its complement the
        # same way, which is the degenerate verdict the runner must not read
        # as "no".
        self.negate = negate
        self.oracle_error = oracle_error
        self.oracle_timeouts: list[float | None] = []
        self.warmups = 0

    def complete(
        self, messages: list[dict[str, Any]], *, timeout_s: float | None = None
    ) -> Completion:
        # warmup_messages carries the image alone, with no instruction ahead of
        # it, so it is the only call with a single message.
        if len(messages) < 2:
            self.warmups += 1
            return Completion("ready", 1)
        instruction = str(messages[1]["content"][0]["text"])
        if instruction.startswith("Look only at"):
            self.oracle_timeouts.append(timeout_s)
            if self.oracle_error:
                return Completion("", 1, error=self.oracle_error)
            holds = self.oracle_holds()
            if self.negate and "something other than" in instruction:
                holds = not holds
            return Completion(
                reply({"action": "terminate", "status": "success" if holds else "fail"}),
                1,
            )
        return Completion(
            next(self.actor_replies), 1, finish_reason=self.finish_reason
        )


class RecordingClient(FakeClient):
    """Keeps every message list handed to the model, to assert on replay."""

    def __init__(self, actor_replies: list[str]) -> None:
        super().__init__(actor_replies)
        self.sent: list[list[dict[str, Any]]] = []

    def complete(
        self, messages: list[dict[str, Any]], *, timeout_s: float | None = None
    ) -> Completion:
        self.sent.append(messages)
        return super().complete(messages, timeout_s=timeout_s)




def test_parser_and_coordinate_conversion() -> None:
    parsed = parse(reply({"action": "click", "coordinate": [250, 750]}))
    assert parsed.action == "click"
    assert parsed.narration == "test action"
    assert to_pixels(parsed.arguments, 100, 200)["coordinate"] == [25, 150]


def test_actor_claim_is_explicitly_unverified(tmp_path: Path) -> None:
    result = run(
        "do something",
        success=None,
        device=FakeDevice(),
        client=FakeClient([reply({"action": "terminate", "status": "success"})]),
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(),
        sleep=lambda _: None,
    )
    assert result["status"] == "actor_claimed_success"
    assert result["verified"] is False
    assert (tmp_path / "run.json").exists()


def test_satisfied_entry_never_touches_device(tmp_path: Path) -> None:
    device = FakeDevice()
    client = FakeClient([], oracle_holds=lambda: True)
    result = run(
        "do something",
        success="the result is visible",
        device=device,
        client=client,
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(),
        sleep=lambda _: None,
    )
    assert result["status"] == "verified"
    assert device.actions == []


def test_false_actor_claim_does_not_pass(tmp_path: Path) -> None:
    device = FakeDevice()
    client = FakeClient(
        [
            reply({"action": "terminate", "status": "success"}),
            reply({"action": "click", "coordinate": [500, 500]}),
            reply({"action": "terminate", "status": "success"}),
        ],
        oracle_holds=lambda: device.changed,
    )
    result = run(
        "tap the control",
        success="the changed screen is visible",
        device=device,
        client=client,
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(max_actions=3),
        sleep=lambda _: None,
    )
    assert result["status"] == "verified"
    assert result["actions"] == 2  # false claim plus click
    assert len(device.actions) == 1


def test_waits_have_their_own_budget(tmp_path: Path) -> None:
    device = FakeDevice()
    client = FakeClient(
        [
            reply({"action": "wait", "time": 0}),
            reply({"action": "click", "coordinate": [500, 500]}),
            reply({"action": "terminate", "status": "success"}),
        ]
    )
    result = run(
        "explore",
        success=None,
        device=device,
        client=client,
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(max_actions=1, max_waits=2),
        sleep=lambda _: None,
    )
    assert result["status"] == "budget_exhausted"
    assert result["actions"] == 1
    assert result["waits"] == 1


def test_unsatisfied_final_check_cannot_pass(tmp_path: Path) -> None:
    result = run(
        "tap the wrong control",
        success="the right result is visible",
        device=FakeDevice(),
        client=FakeClient(
            [reply({"action": "click", "coordinate": [100, 100]})],
            oracle_holds=lambda: False,
        ),
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(max_actions=1),
        sleep=lambda _: None,
    )
    assert result["status"] == "budget_exhausted"
    assert result["verified"] is False


def test_history_replays_the_action_without_the_reasoning(tmp_path: Path) -> None:
    """Reasoning is the larger half of a reply and never changes the next
    decision, so it is dropped from what the next prompt carries."""
    device = FakeDevice()
    client = RecordingClient(
        [
            "Thought: The control sits in the middle of the screen.\n"
            + reply({"action": "click", "coordinate": [500, 500]}),
            reply({"action": "terminate", "status": "success"}),
        ]
    )
    run(
        "tap the control",
        success=None,
        device=device,
        client=client,
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(max_actions=2),
        sleep=lambda _: None,
    )
    replayed = [
        message["content"]
        for messages in client.sent
        for message in messages
        if message.get("role") == "assistant"
    ]
    assert replayed, "the second turn should replay the first"
    assert all("sits in the middle" not in text for text in replayed)
    assert all("mobile_use" in text for text in replayed)
    # The full reply is still on disk, reasoning included.
    assert "sits in the middle" in (tmp_path / "turn_000.raw.txt").read_text()


def test_turn_records_carry_transport_diagnostics(tmp_path: Path) -> None:
    """A reply truncated at max_tokens and a refusal to act both surface as
    "no tool_call block"; only finish_reason tells them apart."""
    run(
        "do something",
        success=None,
        device=FakeDevice(),
        client=FakeClient([reply({"action": "terminate", "status": "success"})]),
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(),
        sleep=lambda _: None,
    )
    record = json.loads((tmp_path / "turn_000.json").read_text())
    assert "finish_reason" in record
    assert "reasoning_chars" in record
    assert "thinking" in record


def test_a_placed_action_leaves_a_marked_screenshot(tmp_path: Path) -> None:
    """The marker goes on the screen the model chose from, so turn_NNN.png and
    turn_NNN.marked.png must be the same shot with and without it."""
    run(
        "do something",
        success=None,
        device=FakeDevice(),
        client=FakeClient(
            [
                # FakeDevice reports 100x200 for a 20x20 png, so the grid point
                # is chosen to land inside the image the marker is drawn on.
                reply({"action": "click", "coordinate": [100, 50]}),
                reply({"action": "terminate", "status": "success"}),
            ]
        ),
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(),
        sleep=lambda _: None,
    )
    marked = tmp_path / "turn_000.marked.png"
    assert json.loads((tmp_path / "turn_000.json").read_text())["marked"] == marked.name
    with Image.open(marked) as drawn, Image.open(tmp_path / "turn_000.png") as plain:
        assert drawn.size == plain.size
        assert drawn.convert("RGB").tobytes() != plain.convert("RGB").tobytes()

    # terminate names no place, so marking it would only duplicate the shot.
    assert not (tmp_path / "turn_001.marked.png").exists()
    assert "marked" not in json.loads((tmp_path / "turn_001.json").read_text())


def test_a_truncated_reply_says_so(tmp_path: Path) -> None:
    result = run(
        "do something",
        success=None,
        device=FakeDevice(),
        client=FakeClient(["Action: I got cut off mid-"], finish_reason="length"),
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(),
        sleep=lambda _: None,
    )
    assert result["status"] == "parse_error"
    assert "MODEL_MAX_TOKENS" in result["detail"]


def _click() -> str:
    return reply({"action": "click", "coordinate": [500, 500]})


def test_actor_prompt_asks_for_reflection(tmp_path: Path) -> None:
    client = RecordingClient([reply({"action": "terminate", "status": "success"})])
    run(
        "do something",
        success=None,
        device=FakeDevice(),
        client=client,
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(),
        sleep=lambda _: None,
    )
    system = client.sent[0][0]["content"]
    assert "Check:" in system
    assert "Expect:" in system
    assert "Go straight to the Check line." in system
    assert "Thought:" not in system


def test_oracle_prompt_does_not_ask_for_reflection(tmp_path: Path) -> None:
    client = RecordingClient([])
    client.oracle_holds = lambda: True
    run(
        "do something",
        success="the result is visible",
        device=FakeDevice(),
        client=client,
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(),
        sleep=lambda _: None,
    )
    oracle = [
        messages
        for messages in client.sent
        if str(messages[1]["content"][0]["text"]).startswith("Look only at")
    ]
    assert oracle
    for messages in oracle:
        assert "Check:" not in messages[0]["content"]
        assert "Expect:" not in messages[0]["content"]


def test_history_replays_check_as_well_as_expect(tmp_path: Path) -> None:
    """The model copies what it is shown. Shown prior turns with no Check, it
    stops writing one - which is what every run on disk did after turn 0."""
    client = RecordingClient(
        [
            "Check: first step\nExpect: the control is pressed\n" + _click(),
            reply({"action": "terminate", "status": "success"}),
        ]
    )
    run(
        "tap the control",
        success=None,
        device=FakeDevice(),
        client=client,
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(max_actions=2),
        sleep=lambda _: None,
    )
    replayed = [
        message["content"]
        for messages in client.sent
        for message in messages
        if message.get("role") == "assistant"
    ]
    assert replayed
    assert all("Check: first step" in text for text in replayed)
    assert all("Expect: the control is pressed" in text for text in replayed)
    record = json.loads((tmp_path / "turn_000.json").read_text())
    assert record["check"] == "first step"
    assert record["expectation"] == "the control is pressed"


def test_a_missing_check_line_is_pointed_out_in_the_next_turn(
    tmp_path: Path,
) -> None:
    """Replaying Check removes the cause; this is what makes it self-healing
    rather than a bet that imitation holds."""
    client = RecordingClient(
        [
            "Check: first step\nExpect: the control is pressed\n" + _click(),
            "Expect: the next control is pressed\n" + _click(),
            "Check: it was pressed\nExpect: done\n" + _click(),
        ]
    )
    run(
        "tap the control",
        success=None,
        device=FakeDevice(),
        client=client,
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(max_actions=3),
        sleep=lambda _: None,
    )

    def now_label(messages: list[dict[str, Any]]) -> str:
        return str(messages[-1]["content"][-1]["text"])

    # Turn 1 followed a reply that had a Check, so it is told nothing extra.
    assert "did not write a Check line" not in now_label(client.sent[1])
    # Turn 2 followed the reply that dropped it.
    assert "did not write a Check line" in now_label(client.sent[2])


def test_turn_zero_is_never_told_off_for_a_missing_check(tmp_path: Path) -> None:
    """There is nothing to check yet, and the prompt asks for "first step"."""
    client = RecordingClient([_click(), _click()])
    run(
        "tap the control",
        success=None,
        device=FakeDevice(),
        client=client,
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(max_actions=2),
        sleep=lambda _: None,
    )
    assert "did not write a Check line" not in str(client.sent[1][-1]["content"])


def test_localized_change_is_moved_with_a_truthful_note(tmp_path: Path) -> None:
    class LocalizedDevice:
        def __init__(self) -> None:
            self.changed = False
            self.actions: list[dict[str, Any]] = []

        def screenshot(self, path: Path) -> tuple[bytes, int, int]:
            image = Image.new("L", (128, 128), color=0)
            if self.changed:
                for y in range(16):
                    for x in range(16):
                        image.putpixel((x, y), 50)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            data = buffer.getvalue()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return data, 128, 128

        def execute(self, action: dict[str, Any]) -> None:
            self.actions.append(action)
            self.changed = True

        def long_press_floor_ms(self) -> int:
            return 400

        def close_app(self, package: str) -> None:
            pass

    client = RecordingClient(
        [_click(), reply({"action": "terminate", "status": "success"})]
    )
    run(
        "tap the control",
        success=None,
        device=LocalizedDevice(),
        client=client,
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(max_actions=2),
        sleep=lambda _: None,
    )
    record = json.loads((tmp_path / "turn_000.json").read_text())
    assert record["moved"] is True
    assert record["screen_delta"] < 0.01
    assert record["screen_tile_max"] > 0.025
    note = client.sent[1][-1]["content"][1]["text"]
    assert "localized screen change was detected" in note
    assert "not proof" in note


def test_reflection_can_be_turned_off(tmp_path: Path) -> None:
    client = RecordingClient([reply({"action": "terminate", "status": "success"})])
    run(
        "do something",
        success=None,
        device=FakeDevice(),
        client=client,
        settings=replace(SETTINGS, model_reflection=False),
        out_dir=tmp_path,
        budget=Budget(),
        sleep=lambda _: None,
    )
    assert "Check:" not in client.sent[0][0]["content"]


def test_console_prints_check_and_expect(capsys: Any) -> None:
    _print_turn(
        {
            "index": 1,
            "narration": "tap Documents",
            "check": "the sheet opened",
            "expectation": "the file list is visible",
            "action": "click",
            "arguments": {"action": "click", "coordinate": [100, 200]},
            "pixels": {"action": "click", "coordinate": [10, 40]},
            "moved": True,
        }
    )
    err = capsys.readouterr().err
    assert "check: the sheet opened" in err
    assert "expect: the file list is visible" in err
    assert "[1] tap Documents" in err


TIMEOUT = "APITimeoutError: Request timed out."


def _verifying_run(tmp_path: Path, client: FakeClient, budget: Budget) -> Any:
    return run(
        "do something",
        success="the result is visible",
        device=FakeDevice(),
        client=client,
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=budget,
        sleep=lambda _: None,
    )


def test_the_oracle_is_not_on_a_shorter_leash_than_the_actor(tmp_path: Path) -> None:
    """It used to get a hard-coded 8s while the actor got MODEL_TIMEOUT_S, so a
    slow first call read as a failed task."""
    client = FakeClient([], oracle_holds=lambda: True)
    _verifying_run(tmp_path, client, Budget())
    assert client.oracle_timeouts
    assert all(value == SETTINGS.model_timeout_s for value in client.oracle_timeouts)


def test_an_explicit_verify_timeout_still_wins(tmp_path: Path) -> None:
    client = FakeClient([], oracle_holds=lambda: True)
    _verifying_run(tmp_path, client, Budget(verify_timeout_s=3))
    assert all(value == 3 for value in client.oracle_timeouts)


def test_an_unreachable_oracle_is_infrastructure_not_a_failed_task(
    tmp_path: Path,
) -> None:
    client = FakeClient(
        [reply({"action": "terminate", "status": "success"})], oracle_error=TIMEOUT
    )
    result = _verifying_run(tmp_path, client, Budget())
    assert result["status"] == "oracle_error"
    assert result["error_class"] == "infrastructure"
    assert result["verified"] is False
    assert result["checks"][-1]["errors"] == [TIMEOUT, TIMEOUT]


def test_a_degenerate_judge_is_inconclusive_not_an_oracle_error(
    tmp_path: Path,
) -> None:
    """Same answer to the predicate and its complement is the judge failing,
    not the transport. Both carry no signal, but they have different fixes,
    and neither is the harness breaking."""
    client = FakeClient(
        [reply({"action": "terminate", "status": "success"})],
        oracle_holds=lambda: True,
        negate=False,
    )
    result = _verifying_run(tmp_path, client, Budget())
    assert result["status"] == "oracle_inconclusive"
    assert result["error_class"] == "oracle"
    assert result["checks"][-1]["kind"] == "inconclusive"
    assert result["checks"][-1]["condition"] == "success"
    assert result["checks"][-1]["negation"] == "success"


def test_an_inconclusive_oracle_leaves_a_stuck_verdict_standing(
    tmp_path: Path,
) -> None:
    """The 2026-09-15 20:55 run. detect_stuck had already caught three
    repeated taps and ended the run correctly; the oracle then answered the
    same way twice and overwrote `stuck` with `oracle_inconclusive` and
    `error_class: infrastructure`, which reads as "the harness broke" when
    what happened is "the agent failed"."""

    class StuckDevice(FakeDevice):
        def screenshot(self, path: Path) -> tuple[bytes, int, int]:
            data = png(0)  # never changes, so every tap repeats
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return data, 100, 200

    client = FakeClient([_click(), _click(), _click()], negate=False)
    result = run(
        "tap the control",
        success="the control is pressed",
        device=StuckDevice(),
        client=client,
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(max_actions=10),
        sleep=lambda _: None,
    )
    assert result["status"] == "stuck"
    assert result["error_class"] == "agent"
    assert "repeated" in result["detail"]
    # The oracle's non-answer is recorded beside the verdict, not instead of it.
    assert "oracle was inconclusive" in result["detail"]
    assert result["checks"][-1]["kind"] == "inconclusive"


def test_a_dead_transport_at_the_stuck_check_still_wins(tmp_path: Path) -> None:
    """The one case where the oracle may overwrite: no verdict arrived at all,
    so nothing about the run was measured."""

    class StuckDevice(FakeDevice):
        def screenshot(self, path: Path) -> tuple[bytes, int, int]:
            data = png(0)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return data, 100, 200

    client = FakeClient([_click(), _click(), _click()], oracle_error=TIMEOUT)
    result = run(
        "tap the control",
        success="the control is pressed",
        device=StuckDevice(),
        client=client,
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(max_actions=10),
        sleep=lambda _: None,
    )
    assert result["status"] == "oracle_error"
    assert result["error_class"] == "infrastructure"


def test_an_inconclusive_oracle_leaves_budget_exhausted_standing(
    tmp_path: Path,
) -> None:
    client = FakeClient([_click(), _click()], negate=False)
    result = run(
        "tap the control",
        success="the control is pressed",
        device=FakeDevice(),
        client=client,
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(max_actions=2),
        sleep=lambda _: None,
    )
    assert result["status"] == "budget_exhausted"
    assert result["error_class"] == "agent"
    assert "oracle was inconclusive" in result["detail"]


def test_error_class_separates_a_dead_harness_from_a_failed_task() -> None:
    assert error_class("verified") is None
    for status in ("device_error", "model_error", "oracle_error"):
        assert error_class(status) == "infrastructure"
    # A judge that answered and could not be read is a third thing. Filing it
    # under infrastructure sent you to restart a server that was working.
    assert error_class("oracle_inconclusive") == "oracle"
    # `timed_out` is no longer produced - the wall-clock deadline is gone -
    # but runs written before that are still on disk and still get read.
    for status in (
        "parse_error",
        "stuck",
        "budget_exhausted",
        "timed_out",
        "actor_gave_up",
        "actor_claimed_success",
    ):
        assert error_class(status) == "agent"


def test_warmup_is_one_extra_call_recorded_in_the_run(tmp_path: Path) -> None:
    client = FakeClient([reply({"action": "terminate", "status": "success"})])
    result = run(
        "do something",
        success=None,
        device=FakeDevice(),
        client=client,
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(warmup=True),
        sleep=lambda _: None,
    )
    assert client.warmups == 1
    assert result["status"] == "actor_claimed_success"
    assert "warmup_ms" in result
    assert "warmup_error" not in result


def test_warmup_is_off_unless_asked_for(tmp_path: Path) -> None:
    client = FakeClient([reply({"action": "terminate", "status": "success"})])
    result = run(
        "do something",
        success=None,
        device=FakeDevice(),
        client=client,
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(),
        sleep=lambda _: None,
    )
    assert client.warmups == 0
    assert "warmup_ms" not in result


def test_a_failed_warmup_does_not_end_the_run(tmp_path: Path) -> None:
    class ExplodingWarmup(FakeClient):
        def complete(
            self, messages: list[dict[str, Any]], *, timeout_s: float | None = None
        ) -> Completion:
            if len(messages) < 2:
                raise RuntimeError("connection refused")
            return super().complete(messages, timeout_s=timeout_s)

    result = run(
        "do something",
        success=None,
        device=FakeDevice(),
        client=ExplodingWarmup([reply({"action": "terminate", "status": "success"})]),
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(warmup=True),
        sleep=lambda _: None,
    )
    assert result["status"] == "actor_claimed_success"
    assert "connection refused" in result["warmup_error"]


def _long_press(**extra: Any) -> str:
    return reply({"action": "long_press", "coordinate": [500, 812], **extra})


def _run_one_hold(tmp_path: Path, instruction: str, reply_text: str) -> tuple[FakeDevice, dict[str, Any]]:
    device = FakeDevice()
    result = run(
        instruction,
        success=None,
        device=device,
        client=FakeClient(
            [reply_text, reply({"action": "terminate", "status": "success"})]
        ),
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(max_actions=2),
        sleep=lambda _: None,
    )
    return device, result


def test_the_instruction_decides_the_hold_the_device_performs(tmp_path: Path) -> None:
    """The point of the whole feature: the number that reaches ADB is the one
    written in the instruction, not the one the model guessed."""
    device, _ = _run_one_hold(
        tmp_path,
        'long press the "Test 14" audio for 3 seconds',
        _long_press(duration_ms=800),
    )
    assert device.actions[0]["duration_ms"] == 3000


def test_the_hold_record_reaches_the_turn_artifact(tmp_path: Path) -> None:
    _run_one_hold(
        tmp_path,
        "long press the audio for 3 seconds",
        _long_press(duration_ms=800),
    )
    held = json.loads((tmp_path / "turn_000.json").read_text())["hold"]
    assert held["ms"] == 3000
    assert held["source"] == "instruction"
    assert held["model_ms"] == 800
    assert held["notes"] == [
        "instruction asked for 3s",
        "model asked for 800ms, overridden by the instruction",
    ]


def test_the_model_still_decides_when_the_instruction_states_no_time(tmp_path: Path) -> None:
    device, _ = _run_one_hold(
        tmp_path, "long press the audio", _long_press(duration_ms=800)
    )
    assert device.actions[0]["duration_ms"] == 800
    assert json.loads((tmp_path / "turn_000.json").read_text())["hold"]["source"] == "model"


def test_seconds_in_the_millisecond_key_are_repaired_before_the_device_sees_them(
    tmp_path: Path,
) -> None:
    """Unrepaired this is a 3ms hold, which Android delivers as a tap while
    every artifact still reads 3."""
    device, _ = _run_one_hold(tmp_path, "long press the audio", _long_press(duration_ms=3))
    assert device.actions[0]["duration_ms"] == 3000
    notes = json.loads((tmp_path / "turn_000.json").read_text())["hold"]["notes"]
    assert "repaired to 3000ms" in notes[0]


def test_a_hold_under_the_device_threshold_is_raised_not_delivered_as_a_tap(
    tmp_path: Path,
) -> None:
    device, _ = _run_one_hold(
        tmp_path, "long press the audio", _long_press(duration_ms=250)
    )
    assert device.actions[0]["duration_ms"] == 400
    assert "delivered as a tap" in json.loads(
        (tmp_path / "turn_000.json").read_text()
    )["hold"]["notes"][0]


def test_the_configured_default_applies_when_nobody_names_a_time(tmp_path: Path) -> None:
    device, _ = _run_one_hold(tmp_path, "long press the audio", _long_press())
    assert device.actions[0]["duration_ms"] == SETTINGS.long_press_ms
    assert json.loads((tmp_path / "turn_000.json").read_text())["hold"]["source"] == "default"


def test_a_duration_that_is_not_a_number_is_the_agents_fault(tmp_path: Path) -> None:
    """It reaches ADB as a crash inside device.execute otherwise, where the
    bare except files a model typo as a dead device and exits 2."""
    device, result = _run_one_hold(
        tmp_path, "long press the audio", _long_press(duration_ms="1s")
    )
    assert result["status"] == "parse_error"
    assert result["error_class"] == "agent"
    assert device.actions == []


def test_a_hold_turn_prints_the_resolved_duration_and_its_source(capsys) -> None:
    _print_turn(
        {
            "index": 0,
            "action": "long_press",
            "arguments": {"action": "long_press", "coordinate": [500, 812], "duration_ms": 800},
            "pixels": {"action": "long_press", "coordinate": [540, 1968], "duration_ms": 3000},
            "hold": {
                "ms": 3000,
                "source": "instruction",
                "notes": ["model asked for 800ms, overridden by the instruction"],
            },
        }
    )
    lines = capsys.readouterr().err.splitlines()
    assert lines[0].endswith("px 540,1968, 3000ms from instruction")
    assert lines[1] == "     hold: model asked for 800ms, overridden by the instruction"


# --- closing the app under test ---


def test_the_app_is_closed_before_the_entry_screenshot(tmp_path: Path) -> None:
    """Order is the whole point. Closing after the capture would hand the
    agent a picture of the previous run's screen and call it the start."""
    order: list[str] = []

    class OrderedDevice(FakeDevice):
        def close_app(self, package: str) -> None:
            order.append(f"close {package}")
            super().close_app(package)

        def screenshot(self, path: Path) -> tuple[bytes, int, int]:
            order.append(f"screenshot {path.name}")
            return super().screenshot(path)

    device = OrderedDevice()
    result = run(
        "do something",
        success=None,
        device=device,
        client=FakeClient([reply({"action": "terminate", "status": "success"})]),
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(),
        sleep=lambda _: None,
    )
    assert order[:2] == ["close com.xuper.chat.app", "screenshot entry.png"]
    assert device.closed == ["com.xuper.chat.app"]
    # Recorded, so a run that started from a state nobody expected can be told
    # apart from one where the close never happened.
    assert result["closed_app"] == "com.xuper.chat.app"


def test_an_empty_package_leaves_the_device_alone(tmp_path: Path) -> None:
    """A run against something other than the app under test."""
    device = FakeDevice()
    result = run(
        "do something",
        success=None,
        device=device,
        client=FakeClient([reply({"action": "terminate", "status": "success"})]),
        settings=replace(SETTINGS, app_package=""),
        out_dir=tmp_path,
        budget=Budget(),
        sleep=lambda _: None,
    )
    assert device.closed == []
    assert "closed_app" not in result


def test_a_close_that_fails_is_infrastructure_not_a_failed_task(
    tmp_path: Path,
) -> None:
    """The agent never got a screen. Exiting 1 here would file a dead ADB
    connection as a task the agent could not do."""

    class DeadDevice(FakeDevice):
        def close_app(self, package: str) -> None:
            raise OSError("adb: device offline")

    result = run(
        "do something",
        success=None,
        device=DeadDevice(),
        client=FakeClient([]),
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(),
        sleep=lambda _: None,
    )
    assert result["status"] == "device_error"
    assert result["error_class"] == "infrastructure"
    assert "com.xuper.chat.app" in result["detail"]
    assert "device offline" in result["detail"]
    assert not (tmp_path / "entry.png").exists()


def test_the_launcher_is_given_time_to_draw_before_the_entry_shot(
    tmp_path: Path,
) -> None:
    """Force-stop returns before the app's window is gone, so a screenshot
    taken straight after catches the app half-faded."""
    slept: list[float] = []
    run(
        "do something",
        success=None,
        device=FakeDevice(),
        client=FakeClient([reply({"action": "terminate", "status": "success"})]),
        settings=replace(SETTINGS, step_sleep_s=1.5),
        out_dir=tmp_path,
        budget=Budget(),
        sleep=slept.append,
    )
    assert slept[0] == 1.5


class Waiting:
    """A clock that only moves when the runner sleeps, so the settle loop is
    measured in polls rather than in how fast this machine ran the test."""

    def __init__(self) -> None:
        self.now = 0.0

    def read(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _settling_run(tmp_path: Path, device: FakeDevice, timeout_s: float) -> Any:
    clock = Waiting()
    return run(
        "tap the control",
        success=None,
        device=device,
        client=FakeClient([_click()]),
        settings=replace(SETTINGS, settle_timeout_s=timeout_s),
        out_dir=tmp_path,
        budget=Budget(max_actions=1),
        clock=clock.read,
        sleep=clock.sleep,
    )


def test_a_settled_capture_records_what_it_cost(tmp_path: Path) -> None:
    _settling_run(tmp_path, FakeDevice(), 2)
    record = json.loads((tmp_path / "turn_000.json").read_text())
    # One poll: the second frame matched the first, so the screen was already
    # still and the turn paid 250 ms to find that out.
    assert record["settle_ms"] == 250.0
    assert record["settled"] is True


def test_a_screen_still_moving_at_the_cap_is_marked(tmp_path: Path) -> None:
    """`settled: false` is the flag on a screenshot that may be half drawn,
    which is the thing worth knowing when the model then misreads it."""

    class Flickering(FakeDevice):
        def __init__(self) -> None:
            super().__init__()
            self.shots = 0

        def screenshot(self, path: Path) -> tuple[bytes, int, int]:
            self.shots += 1
            data = png(255 if self.shots % 2 else 0)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return data, 100, 200

    _settling_run(tmp_path, Flickering(), 1)
    record = json.loads((tmp_path / "turn_000.json").read_text())
    assert record["settle_ms"] == 1000.0
    assert record["settled"] is False


def test_the_settle_keys_are_absent_when_the_wait_is_off(tmp_path: Path) -> None:
    """Not `settled: false` on every turn, which would read as "the screen
    never stopped moving" for a run that never asked."""
    _settling_run(tmp_path, FakeDevice(), 0)
    record = json.loads((tmp_path / "turn_000.json").read_text())
    assert "settle_ms" not in record
    assert "settled" not in record
