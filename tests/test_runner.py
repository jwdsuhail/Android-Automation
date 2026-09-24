from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from android_runner.cases import Checkpoint
from android_runner.client import Completion
from android_runner.cli import _print_turn
from android_runner.qwen_vl import parse, to_pixels
from android_runner.runner import UNMEASURED, Budget, error_class, run
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
        self.questions: list[str] = []
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
            self.questions.append(instruction)
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
    assert [
        (check["phase"], check["screenshot"], check["turn"])
        for check in result["checks"]
    ] == [
        ("actor_claim", "verify_000.png", 0),
        ("actor_claim", "verify_002.png", 2),
    ]
    assert (tmp_path / "verify_000.png").is_file()
    assert (tmp_path / "verify_002.png").is_file()


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
    final = result["checks"][-1]
    assert (final["phase"], final["screenshot"], final["turn"]) == (
        "final",
        "turn_000.after.png",
        0,
    )


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
    client = RecordingClient([reply({"action": "terminate", "status": "success"})])
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
    client = FakeClient(
        [reply({"action": "terminate", "status": "success"})], oracle_holds=lambda: True
    )
    _verifying_run(tmp_path, client, Budget())
    assert client.oracle_timeouts
    assert all(value == SETTINGS.model_timeout_s for value in client.oracle_timeouts)


def test_an_explicit_verify_timeout_still_wins(tmp_path: Path) -> None:
    client = FakeClient(
        [reply({"action": "terminate", "status": "success"})], oracle_holds=lambda: True
    )
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
    assert (
        result["checks"][-1]["phase"],
        result["checks"][-1]["screenshot"],
        result["checks"][-1]["turn"],
    ) == ("stuck", "turn_002.after.png", 2)


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


# --- mid-run checkpoints --------------------------------------------------
#
# The same oracle, at a few named steps. Everything below is about the two
# properties that make that worth having: a rung is only put to the judge when
# the actor says it is there, and a rung nobody could get an answer about is
# never reported as an agent that skipped a step.


SUCCESS = "the result is visible"
LOGGED_OUT = "the login screen is visible"

# The success condition, asked once at the end of the run. These tests are
# about which named steps the run went through, so the ending always holds.
ARRIVES = True


def a_rung(**overrides: Any) -> Checkpoint:
    fields: dict[str, Any] = {
        "id": "logged-out",
        "condition": LOGGED_OUT,
        "after": "log ?out",
    }
    fields.update(overrides)
    return Checkpoint(**fields)


def saying(narration: str) -> str:
    """An actor reply with a chosen narration, which is what triggers a rung.

    Each tap lands somewhere its own narration decides, so that a sequence of
    them is not read as the agent repeating itself. What is under test here is
    the trigger, not the stuck detector.
    """
    action = {
        "action": "click",
        "coordinate": [100 + sum(map(ord, narration)) % 800, 500],
    }
    return f"Action: {narration}\n" + reply(action)[len("Action: test action\n") :]


class Judge(FakeClient):
    """A judge with an opinion per condition, and per time asked.

    The success condition and a rung are different questions put to the same
    model, so a test that cannot answer them differently cannot tell the two
    apart at all. A list is read one answer per pair, holding the last: how a
    screen that was not logged out yet becomes one two taps later.
    """

    def __init__(
        self,
        actor_replies: list[str],
        holds: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(actor_replies, **kwargs)
        self.queued = {
            phrase: list(answer) if isinstance(answer, list) else [answer]
            for phrase, answer in (holds or {}).items()
        }
        self.current: dict[str, bool | None] = {}

    def _phrase(self, question: str) -> str | None:
        return next((text for text in self.queued if text in question), None)

    def _answer(self, question: str) -> bool | None:
        phrase = self._phrase(question)
        if phrase is None:
            return False
        # The predicate opens a pair; its complement must be answered from the
        # same opinion or every verdict would pair as inconclusive.
        if "something other than" not in question:
            queued = self.queued[phrase]
            self.current[phrase] = queued.pop(0) if len(queued) > 1 else queued[0]
        return self.current.get(phrase, False)

    def complete(
        self, messages: list[dict[str, Any]], *, timeout_s: float | None = None
    ) -> Completion:
        instruction = (
            str(messages[1]["content"][0]["text"]) if len(messages) > 1 else ""
        )
        if not instruction.startswith("Look only at"):
            return super().complete(messages, timeout_s=timeout_s)
        self.questions.append(instruction)
        self.oracle_timeouts.append(timeout_s)
        held = self._answer(instruction)
        if held is None:
            # Answers, unusably: the same answer to the predicate and to its
            # complement, which is the degenerate verdict the runner has to
            # read as no measurement rather than as a no.
            return Completion(reply({"action": "terminate", "status": "success"}), 1)
        if "something other than" in instruction:
            held = not held
        return Completion(
            reply({"action": "terminate", "status": "success" if held else "fail"}), 1
        )


def asked_about(client: FakeClient, condition: str) -> int:
    """How many questions the judge was put about one condition."""
    return sum(1 for question in client.questions if condition in question)


def _checkpoint_run(
    tmp_path: Path,
    client: FakeClient,
    checkpoints: tuple[Checkpoint, ...],
    *,
    max_actions: int = 4,
    budget: Budget | None = None,
    device: FakeDevice | None = None,
) -> dict[str, Any]:
    return run(
        "log out and back in",
        success=SUCCESS,
        device=device or FakeDevice(),
        client=client,
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=budget or Budget(max_actions=max_actions),
        checkpoints=checkpoints,
        sleep=lambda _: None,
    )


def test_a_turn_naming_no_checkpoint_costs_no_oracle_call(tmp_path: Path) -> None:
    """The whole economy of the feature. Polling every frame that moved would
    have added 46-50 calls to a real run; this adds two, at the named step."""
    client = Judge(
        [saying("Tap the Profile icon"), saying("Tap the Settings row")],
        holds={SUCCESS: ARRIVES, LOGGED_OUT: True},
    )
    result = _checkpoint_run(tmp_path, client, (a_rung(),), max_actions=2)
    # Never named, so it was asked once at the end and nowhere in the loop.
    assert asked_about(client, LOGGED_OUT) == 2
    assert result["checkpoints"][0]["triggered"] is False


def test_a_rung_is_measured_on_the_screen_the_named_turn_left(tmp_path: Path) -> None:
    client = Judge(
        [saying("Tap the red Log out button"), saying("Tap Sign in")],
        holds={SUCCESS: ARRIVES, LOGGED_OUT: True},
    )
    result = _checkpoint_run(tmp_path, client, (a_rung(),), max_actions=2)

    assert result["status"] == "verified"
    rung = result["checkpoints"][0]
    assert (rung["met"], rung["triggered"], rung["turn"], rung["polls"]) == (
        True,
        True,
        0,
        1,
    )
    assert rung["screenshot"] == "turn_000.after.png"
    crossing = [c for c in result["checks"] if c["phase"] == "checkpoint"]
    assert len(crossing) == 1
    assert crossing[0]["checkpoint_id"] == "logged-out"
    assert crossing[0]["checkpoint_index"] == 0
    # Met on turn 0 and never put again, though the run continues after it.
    assert asked_about(client, LOGGED_OUT) == 2


def test_a_rung_that_did_not_hold_is_asked_again_when_it_is_named_again(
    tmp_path: Path,
) -> None:
    """A confirm dialog is why. "Tap Log out" names the button and then the
    dialog's button, and the first of those screens is not logged out yet. The
    pattern re-checks on its own, with nothing hand-tuned."""
    client = Judge(
        [
            saying("Tap Log out"),
            saying("Tap Log out to confirm"),
            saying("Tap Sign in"),
        ],
        holds={SUCCESS: ARRIVES, LOGGED_OUT: [False, True]},
    )
    result = _checkpoint_run(tmp_path, client, (a_rung(),), max_actions=3)

    rung = result["checkpoints"][0]
    assert (rung["met"], rung["polls"], rung["turn"]) == (True, 2, 1)
    assert rung["screenshot"] == "turn_001.after.png"
    assert result["status"] == "verified"


def test_a_rung_may_not_be_asked_more_often_than_its_cap(tmp_path: Path) -> None:
    """A pattern matching every turn is a mistake in the case, not a licence
    to spend the run on one question."""
    client = Judge(
        [
            saying("Log out step one"),
            saying("Log out step two"),
            saying("Log out step three"),
        ],
        holds={SUCCESS: ARRIVES, LOGGED_OUT: False},
    )
    result = _checkpoint_run(tmp_path, client, (a_rung(max_polls=2),), max_actions=3)
    assert result["checkpoints"][0]["polls"] == 2
    assert asked_about(client, LOGGED_OUT) == 4  # two pairs, not three


def test_a_rung_nothing_named_is_asked_once_at_the_end(tmp_path: Path) -> None:
    """So an oddly-narrated turn is not the sole reason a step reads missed."""
    client = Judge(
        [saying("Tap the thing")], holds={SUCCESS: ARRIVES, LOGGED_OUT: True}
    )
    result = _checkpoint_run(tmp_path, client, (a_rung(),), max_actions=1)
    rung = result["checkpoints"][0]
    assert (rung["triggered"], rung["met"], rung["polls"]) == (False, True, 1)
    assert result["status"] == "verified"
    assert result["checks"][-1]["phase"] == "checkpoint"


def test_a_required_step_the_run_skipped_is_not_a_pass(tmp_path: Path) -> None:
    """The failure this exists for. Both log-out runs on disk were verified on
    a two-item condition whose first item - the logout - cannot be seen on the
    screen that proved the second."""
    client = Judge(
        [saying("Tap the thing")], holds={SUCCESS: ARRIVES, LOGGED_OUT: False}
    )
    result = _checkpoint_run(tmp_path, client, (a_rung(),), max_actions=1)
    assert result["status"] == "checkpoints_incomplete"
    assert result["verified"] is False
    assert result["error_class"] == "agent"
    assert "logged-out" in result["detail"]


def test_an_observational_step_never_holds_back_a_pass(tmp_path: Path) -> None:
    """For a genuinely transient rung - a coachmark shown once and dismissed by
    the next tap - where a miss is as likely the camera as the agent."""
    client = Judge(
        [saying("Tap the thing")], holds={SUCCESS: ARRIVES, LOGGED_OUT: False}
    )
    result = _checkpoint_run(tmp_path, client, (a_rung(required=False),), max_actions=1)
    assert result["status"] == "verified"
    assert result["checkpoints"][0]["met"] is False


def test_a_judge_that_would_not_answer_is_never_the_agents_fault(
    tmp_path: Path,
) -> None:
    """The property the rest of this runner is built around, extended to rungs.
    An unreadable answer about a step says nothing about the agent, so it must
    not exit the way a step the agent skipped exits."""
    client = Judge([saying("Tap Log out")], holds={SUCCESS: ARRIVES, LOGGED_OUT: None})
    result = _checkpoint_run(tmp_path, client, (a_rung(),), max_actions=1)
    assert result["status"] == "oracle_inconclusive"
    assert result["error_class"] == "oracle"
    assert error_class(result["status"]) in UNMEASURED
    assert result["checkpoints"][0]["met"] is False


def test_a_dead_transport_on_a_rung_is_infrastructure(tmp_path: Path) -> None:
    """Distinguished from the above because the fix is different: one is a
    judge to re-ask, the other a machine to repair."""

    class DeadOnRung(Judge):
        def complete(
            self, messages: list[dict[str, Any]], *, timeout_s: float | None = None
        ) -> Completion:
            question = (
                str(messages[1]["content"][0]["text"]) if len(messages) > 1 else ""
            )
            if LOGGED_OUT in question:
                self.questions.append(question)
                return Completion("", 1, error="Timeout")
            return super().complete(messages, timeout_s=timeout_s)

    client = DeadOnRung([saying("Tap Log out")], holds={SUCCESS: ARRIVES})
    result = _checkpoint_run(tmp_path, client, (a_rung(),), max_actions=1)
    assert result["status"] == "oracle_error"
    assert result["error_class"] == "infrastructure"


def test_an_unusable_answer_still_spends_the_rungs_budget(tmp_path: Path) -> None:
    """It cost two model calls and looked at the same screen. Not counting it
    is how one unreadable rung spends the whole run; the run still ends on an
    oracle status, so nothing is laid at the agent's door for it."""
    client = Judge(
        [saying("Log out one"), saying("Log out two"), saying("Log out three")],
        holds={SUCCESS: ARRIVES, LOGGED_OUT: None},
    )
    result = _checkpoint_run(tmp_path, client, (a_rung(max_polls=2),), max_actions=3)
    assert result["checkpoints"][0]["polls"] == 2
    assert result["status"] == "oracle_inconclusive"


def test_a_whole_run_ceiling_bounds_many_rungs(tmp_path: Path) -> None:
    rungs = tuple(
        a_rung(id=f"rung-{n}", condition=f"screen {n} is visible")
        for n in range(3)
    )
    client = Judge(
        [saying("Log out now")],
        holds={SUCCESS: ARRIVES, **{f"screen {n} is visible": False for n in range(3)}},
    )
    result = _checkpoint_run(
        tmp_path,
        client,
        rungs,
        budget=Budget(max_actions=1, max_checkpoint_polls=2),
    )
    assert sum(rung["polls"] for rung in result["checkpoints"]) == 2
    assert [rung["triggered"] for rung in result["checkpoints"]] == [True] * 3
    # The rung the ceiling cut off was never measured, so the run does not get
    # to call it a step the agent missed.
    assert result["status"] == "oracle_inconclusive"


def test_a_case_with_no_checkpoints_writes_exactly_what_it_used_to(
    tmp_path: Path,
) -> None:
    """Every case on disk today. No key, no calls, no change of verdict."""
    client = FakeClient(
        [reply({"action": "terminate", "status": "success"})], oracle_holds=lambda: True
    )
    result = _checkpoint_run(tmp_path, client, (), max_actions=2)
    assert result["status"] == "verified"
    assert "checkpoints" not in result
    assert "checkpoints" not in json.loads((tmp_path / "run.json").read_text())


def test_checkpoints_are_ignored_without_a_success_condition(tmp_path: Path) -> None:
    """They say how a run reached the condition, not whether it did. `cases.py`
    refuses the combination at the door; a caller who builds a run by hand gets
    the same answer rather than a graded rung nothing reads."""
    client = Judge([reply({"action": "terminate", "status": "success"})])
    result = run(
        "explore",
        success=None,
        device=FakeDevice(),
        client=client,
        settings=SETTINGS,
        out_dir=tmp_path,
        budget=Budget(max_actions=1),
        checkpoints=(a_rung(),),
        sleep=lambda _: None,
    )
    assert result["status"] == "actor_claimed_success"
    assert "checkpoints" not in result
    assert client.questions == []


def test_a_bad_trigger_pattern_leaves_a_run_rather_than_a_crash(
    tmp_path: Path,
) -> None:
    """`Case.validate()` refuses it at the door, so this is the hand-built
    caller. A run that dies here writes no run.json at all, which is the one
    outcome worse than a rung that never fires."""
    client = Judge(
        [saying("Tap Log out")], holds={SUCCESS: ARRIVES, LOGGED_OUT: True}
    )
    result = _checkpoint_run(tmp_path, client, (a_rung(after="log(out"),), max_actions=1)
    rung = result["checkpoints"][0]
    assert rung["trigger_error"]
    # Never fired, so the end-of-run question is what measured it.
    assert (rung["triggered"], rung["met"]) == (False, True)
    assert result["status"] == "verified"


def test_the_screen_a_success_claim_is_judged_on_is_settled(tmp_path: Path) -> None:
    """It was the one frame a verdict is read from that was a single shot.

    The settle loop is off in the test settings, so this run turns it on: with
    it off there is nothing to tell `capture` and `capture_settled` apart, and
    that is exactly the difference under test.
    """

    class Counting(FakeDevice):
        def __init__(self) -> None:
            super().__init__()
            self.shots: list[str] = []

        def screenshot(self, path: Path) -> tuple[bytes, int, int]:
            self.shots.append(path.name)
            return super().screenshot(path)

    device = Counting()
    client = Judge(
        [reply({"action": "terminate", "status": "success"})], holds={SUCCESS: ARRIVES}
    )
    result = run(
        "do something",
        success=SUCCESS,
        device=device,
        client=client,
        settings=replace(SETTINGS, settle_timeout_s=1.0),
        out_dir=tmp_path,
        budget=Budget(max_actions=2),
        sleep=lambda _: None,
    )
    assert result["status"] == "verified"
    # Settling shoots the same filename until two frames match, so the claim
    # frame costs more than the one shot it used to.
    assert device.shots.count("verify_000.png") > 1
