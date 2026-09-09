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
from android_runner.runner import Budget, run
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

    def screenshot(self, path: Path) -> tuple[bytes, int, int]:
        data = png(255 if self.changed else 0)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data, 100, 200

    def execute(self, action: dict[str, Any]) -> None:
        self.actions.append(action)
        if action["action"] != "wait":
            self.changed = True


class FakeClient:
    def __init__(
        self,
        actor_replies: list[str],
        oracle_holds: Callable[[], bool] = lambda: False,
        finish_reason: str = "stop",
    ) -> None:
        self.actor_replies = iter(actor_replies)
        self.oracle_holds = oracle_holds
        self.finish_reason = finish_reason

    def complete(
        self, messages: list[dict[str, Any]], *, timeout_s: float | None = None
    ) -> Completion:
        instruction = str(messages[1]["content"][0]["text"])
        if instruction.startswith("Look only at"):
            holds = self.oracle_holds()
            if "NOT the case" in instruction:
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


def test_history_replays_expect_and_drops_check(tmp_path: Path) -> None:
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
    assert all("Check:" not in text for text in replayed)
    assert all("Expect: the control is pressed" in text for text in replayed)
    record = json.loads((tmp_path / "turn_000.json").read_text())
    assert record["check"] == "first step"
    assert record["expectation"] == "the control is pressed"


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
