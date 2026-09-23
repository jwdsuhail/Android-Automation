"""Parsing, replay, history, and coordinates for the Qwen3-VL reply shape.

Most cases here are regressions: each one is a way a real server returned
something the naive parser got wrong.
"""

from __future__ import annotations

import base64
import io
import json

import pytest
from PIL import Image

from android_runner import qwen_vl


def png(value: int = 0) -> bytes:
    buffer = io.BytesIO()
    Image.new("L", (8, 8), color=value).save(buffer, format="PNG")
    return buffer.getvalue()


def call(action: dict, tag: str = "tool_call") -> str:
    body = json.dumps({"name": "mobile_use", "arguments": action})
    if tag == "fenced":
        return f"```json\n{body}\n```"
    open_tag, close_tag = ("<tool_call>", "</tool_call>") if tag == "tool_call" else ("[tool_call]", "[/tool_call]")
    return f"{open_tag}{body}{close_tag}"


CLICK = {"action": "click", "coordinate": [500, 500]}


# --- prompt -----------------------------------------------------------------


def test_no_think_prompt_is_derived_not_duplicated() -> None:
    assert "Thought:" not in qwen_vl.NO_THINK_SYSTEM_PROMPT
    assert "Go straight to the Action line" in qwen_vl.NO_THINK_SYSTEM_PROMPT
    # The parts that are not about thinking must survive the derivation.
    assert "There is no command that launches an app by name" in qwen_vl.NO_THINK_SYSTEM_PROMPT
    assert "hold to confirm" in qwen_vl.NO_THINK_SYSTEM_PROMPT


def test_system_prompt_selects_on_the_thinking_flag() -> None:
    assert qwen_vl.system_prompt(True) is qwen_vl.SYSTEM_PROMPT
    assert qwen_vl.system_prompt(False) is qwen_vl.NO_THINK_SYSTEM_PROMPT


def test_no_think_reflection_asks_for_check_expect_not_thought() -> None:
    prompt = qwen_vl.system_prompt(False, reflection=True)
    assert "Thought:" not in prompt
    assert "Check:" in prompt
    assert "Expect:" in prompt
    assert "Go straight to the Check line." in prompt
    assert "first step" in prompt
    assert "rather than terminate" in prompt
    assert "There is no command that launches an app by name" in prompt


def test_thinking_reflection_keeps_thought_and_adds_check_expect() -> None:
    prompt = qwen_vl.system_prompt(True, reflection=True)
    assert "Thought:" in prompt
    assert "Check:" in prompt
    assert "Expect:" in prompt
    assert "a Thought line, a Check line, an Expect line, an Action line" in prompt


def test_reflection_off_leaves_the_plain_actor_prompt() -> None:
    prompt = qwen_vl.system_prompt(False, reflection=False)
    assert "Check:" not in prompt
    assert "Expect:" not in prompt
    assert "Go straight to the Action line." in prompt


def test_the_oracle_prompt_carries_no_action_space() -> None:
    """A grader that inherits the actor's prompt inherits its last line - "do
    not terminate with status success unless the requested task is complete" -
    which is a standing refusal to answer the complement question. That
    contradiction is what collapsed the pair to (fail, fail)."""
    prompt = qwen_vl.system_prompt(False, reflection=False, oracle=True)
    assert prompt is qwen_vl.ORACLE_SYSTEM_PROMPT
    assert "## Action space" not in prompt
    assert '"action": "click"' not in prompt
    assert '"action": "swipe"' not in prompt
    assert "To open an app" not in prompt
    assert "If the screen is busy" not in prompt
    assert "Do not terminate with status success unless" not in prompt
    # It still has to answer in the envelope the parser reads.
    assert "<tool_call>" in prompt
    assert '"action": "terminate", "status": "success|fail"' in prompt


def test_the_oracle_prompt_ignores_the_actor_flags() -> None:
    """It is not a variant of the actor prompt, so neither flag reaches it."""
    for thinking in (True, False):
        for reflection in (True, False):
            assert (
                qwen_vl.system_prompt(thinking, reflection, oracle=True)
                is qwen_vl.ORACLE_SYSTEM_PROMPT
            )


def test_every_actor_prompt_says_a_half_drawn_screen_is_busy() -> None:
    """The deterministic settle is the mechanism; this is the supplement, for
    the draw that outlasts the cap."""
    for prompt in (
        qwen_vl.SYSTEM_PROMPT,
        qwen_vl.NO_THINK_SYSTEM_PROMPT,
        qwen_vl.system_prompt(False, reflection=True),
        qwen_vl.system_prompt(True, reflection=True),
    ):
        assert "only part drawn is busy too" in prompt


# --- parsing ----------------------------------------------------------------


@pytest.mark.parametrize("tag", ["tool_call", "bracketed", "fenced"])
def test_parse_accepts_every_block_spelling(tag: str) -> None:
    parsed = qwen_vl.parse(f"Action: tap it\n{call(CLICK, tag)}")
    assert parsed.action == "click"
    assert parsed.narration == "tap it"


def test_parse_splits_a_dangling_close_tag() -> None:
    """Ollama prefills the opening <think> into the prompt, so only the
    closing tag comes back (ollama/ollama#12593)."""
    raw = f"I should tap the icon.\n</think>\nAction: tap it\n{call(CLICK)}"
    parsed = qwen_vl.parse(raw)
    assert parsed.thinking == "I should tap the icon."
    assert parsed.narration == "tap it"
    assert parsed.action == "click"


def test_a_dangling_close_after_the_call_is_not_treated_as_reasoning() -> None:
    """Splitting there would discard the tool call and misreport the failure."""
    parsed = qwen_vl.parse(f"Action: tap it\n{call(CLICK)}\n</think>")
    assert parsed.action == "click"


def test_parse_strips_a_paired_think_block() -> None:
    raw = f"<think>weighing options</think>\nAction: tap it\n{call(CLICK)}"
    parsed = qwen_vl.parse(raw)
    assert parsed.thinking == "weighing options"
    assert "<think>" not in (parsed.narration or "")


def test_a_separate_reasoning_field_wins_over_inline_text() -> None:
    parsed = qwen_vl.parse(f"Action: tap it\n{call(CLICK)}", "from the reasoning channel")
    assert parsed.thinking == "from the reasoning channel"


def test_prose_above_the_action_line_is_reasoning_even_unlabelled() -> None:
    """A server that routes thinking to its own channel eats the label."""
    parsed = qwen_vl.parse(f"The plus button is top right.\nAction: tap it\n{call(CLICK)}")
    assert parsed.thinking == "The plus button is top right."
    assert parsed.narration == "tap it"


def test_a_bare_sentence_becomes_narration_not_nothing() -> None:
    parsed = qwen_vl.parse(f"Tap on the plus icon in the top right corner.\n{call(CLICK)}")
    assert parsed.narration == "Tap on the plus icon in the top right corner."


def test_a_bare_action_label_does_not_swallow_the_tool_call() -> None:
    """A model told to go straight to the Action line writes exactly this
    shape; `\\s` in the regex used to capture the JSON as the narration."""
    parsed = qwen_vl.parse(f"Action:\n{call(CLICK)}")
    assert parsed.action == "click"
    assert parsed.narration != "Action:"
    assert "mobile_use" not in (parsed.narration or "")


def test_identical_thought_and_action_lines_collapse_to_one() -> None:
    parsed = qwen_vl.parse(f"Thought: Tap it\nAction: Tap it\n{call(CLICK)}")
    assert parsed.thinking == "Tap it"
    assert parsed.narration is None


def test_a_nested_thought_label_does_not_leak_to_narration() -> None:
    parsed = qwen_vl.parse(f"Action: Thought: Tap it\n{call(CLICK)}")
    assert parsed.narration is None
    assert parsed.thinking == "Tap it"


def test_missing_block_is_a_clear_error() -> None:
    with pytest.raises(ValueError, match="no tool_call block"):
        qwen_vl.parse("Action: I am not going to act")


def test_invalid_json_says_so() -> None:
    with pytest.raises(ValueError, match="invalid tool_call JSON"):
        qwen_vl.parse("<tool_call>{not json}</tool_call>")


def test_wrong_tool_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="mobile_use"):
        qwen_vl.parse('<tool_call>{"name": "computer_use", "arguments": {}}</tool_call>')


def test_answer_is_not_an_allowed_action() -> None:
    """device.execute has no branch for it and would raise mid-run."""
    assert "answer" not in qwen_vl.ALLOWED_ACTIONS
    with pytest.raises(ValueError, match="unsupported action"):
        qwen_vl.parse(call({"action": "answer", "text": "hello"}))


def test_drag_is_allowed_because_the_device_executes_it() -> None:
    assert "drag" in qwen_vl.ALLOWED_ACTIONS


# --- replay -----------------------------------------------------------------


def test_replay_drops_the_thought_and_keeps_the_action() -> None:
    raw = f"Thought: The plus button is top right and opens the sheet.\nAction: tap it\n{call(CLICK)}"
    replayed = qwen_vl.replay(raw)
    assert replayed.startswith("Action: tap it")
    assert "top right" not in replayed
    assert "mobile_use" in replayed
    # Still parses: the next turn's history has to remain readable.
    assert qwen_vl.parse(replayed).action == "click"


def test_replay_drops_a_think_block() -> None:
    raw = f"<think>long deliberation</think>\nAction: tap it\n{call(CLICK)}"
    assert "long deliberation" not in qwen_vl.replay(raw)


def test_replay_keeps_a_reply_that_has_no_reasoning() -> None:
    raw = f"Action: tap it\n{call(CLICK)}"
    assert qwen_vl.replay(raw) == raw


def test_parse_extracts_check_expect_action() -> None:
    raw = (
        "Check: first step\n"
        "Expect: the plus sheet is open\n"
        "Action: tap the plus button\n"
        f"{call(CLICK)}"
    )
    parsed = qwen_vl.parse(raw)
    assert parsed.check == "first step"
    assert parsed.expectation == "the plus sheet is open"
    assert parsed.narration == "tap the plus button"
    assert parsed.thinking is None
    assert parsed.action == "click"


def test_check_expect_are_not_treated_as_reasoning() -> None:
    raw = (
        "Check: the sheet did not open\n"
        "Expect: the documents row is highlighted\n"
        "Action: tap Documents\n"
        f"{call(CLICK)}"
    )
    parsed = qwen_vl.parse(raw)
    assert parsed.thinking is None
    assert parsed.check == "the sheet did not open"


def test_replay_keeps_the_check_line() -> None:
    """Stripping it is what killed it. A model shown prior turns that never
    carry a Check writes no Check either, and every run on disk shows the line
    surviving turn 0 and gone by turn 1."""
    raw = (
        "Thought: the sheet is closed\n"
        "Check: first step\n"
        "Expect: the plus sheet is open\n"
        "Action: tap the plus button\n"
        f"{call(CLICK)}"
    )
    replayed = qwen_vl.replay(raw)
    assert replayed.startswith("Check: first step")
    assert "Thought:" not in replayed  # reasoning is still dropped
    parsed = qwen_vl.parse(replayed)
    assert parsed.check == "first step"
    assert parsed.expectation == "the plus sheet is open"
    assert parsed.action == "click"


def test_replay_falls_back_through_expect_to_action() -> None:
    raw = f"Thought: it is right there\nExpect: the sheet opens\n{call(CLICK)}"
    assert qwen_vl.replay(raw).startswith("Expect: the sheet opens")
    raw = f"Thought: it is right there\nAction: tap it\n{call(CLICK)}"
    assert qwen_vl.replay(raw).startswith("Action: tap it")


def test_a_nested_expect_label_does_not_leak_to_narration() -> None:
    parsed = qwen_vl.parse(f"Action: Expect: the sheet opens\n{call(CLICK)}")
    assert parsed.narration is None
    assert parsed.expectation == "the sheet opens"


def test_oracle_terminate_parses_without_check_expect() -> None:
    parsed = qwen_vl.parse(
        'Action: terminate with status fail\n[tool_call]\n'
        '{"name": "mobile_use", "arguments": {"status": "fail"}}\n[/tool_call]'
    )
    assert parsed.action == "terminate"
    assert parsed.check is None
    assert parsed.expectation is None


# --- history ----------------------------------------------------------------


def messages_for(turns: int, history_n: int) -> list[dict]:
    history = [(png(i), f"Action: step {i}\n{call(CLICK)}") for i in range(turns)]
    return qwen_vl.build_messages("do it", png(9), history, history_n)


def text_of(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    return " ".join(
        part["text"] for part in content if part.get("type") == "text"
    )


def test_old_images_are_dropped_but_the_action_trail_survives() -> None:
    """Dropping the pair together erases what the agent already tried, which
    is what makes it retry the same failing tap. The trail survives as one
    `user` message rather than as the assistant turns it came from."""
    messages = messages_for(turns=5, history_n=3)
    images = [m for m in messages if qwen_vl._is_image_message(m)]
    assert len(images) == 3
    # Turns 2, 3 and 4 still have their screens, so they stay as themselves.
    assistants = [m for m in messages if m.get("role") == "assistant"]
    assert len(assistants) == 2
    trail = [
        m
        for m in messages
        if m.get("role") == "user" and qwen_vl.TRAIL_LABEL in text_of(m)
    ]
    assert len(trail) == 1
    # Nothing the agent did is forgotten, wherever it now lives.
    whole = " ".join(text_of(m) for m in messages)
    assert [f"step {i}" in whole for i in range(5)] == [True] * 5


def test_no_two_adjacent_assistant_messages_survive_the_cap() -> None:
    """The defect this is the direct check for: dropping an image left its
    assistant turn behind, so a 27-turn run sent 24 consecutive `assistant`
    messages, each about a screen no longer in the prompt and with no `user`
    turn between them. No chat template was trained on that shape."""
    for turns in (1, 2, 5, 12, 27):
        messages = messages_for(turns=turns, history_n=3)
        roles = [m.get("role") for m in messages]
        pairs = list(zip(roles, roles[1:]))
        assert ("assistant", "assistant") not in pairs, (turns, roles)


def test_the_folded_trail_sits_where_the_dropped_turns_were() -> None:
    """Ahead of the screenshots that survived, so the prompt still reads
    oldest to newest."""
    messages = messages_for(turns=5, history_n=3)
    roles = [m.get("role") for m in messages]
    first_image = next(
        i for i, m in enumerate(messages) if qwen_vl._is_image_message(m)
    )
    trail_at = next(
        i
        for i, m in enumerate(messages)
        if m.get("role") == "user" and qwen_vl.TRAIL_LABEL in text_of(m)
    )
    assert roles[0] == "system"
    assert trail_at < first_image


def test_history_n_counts_the_current_screenshot() -> None:
    messages = messages_for(turns=5, history_n=1)
    assert len([m for m in messages if qwen_vl._is_image_message(m)]) == 1


def test_build_messages_anchors_the_two_screens() -> None:
    messages = qwen_vl.build_messages("do it", png(9), [(png(1), "Action: x")], 3)
    labels = [
        part["text"]
        for message in messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "text"
    ]
    assert qwen_vl.BEFORE_LABEL in labels
    assert qwen_vl.NOW_LABEL in labels


def test_build_messages_history_n3_keeps_before_t2_t1_now() -> None:
    history = [(png(i), f"Expect: e{i}\nAction: step {i}\n{call(CLICK)}") for i in range(3)]
    messages = qwen_vl.build_messages("do it", png(9), history, 3)
    images = [m for m in messages if qwen_vl._is_image_message(m)]
    assert len(images) == 3
    values = []
    labels = []
    for message in images:
        url = message["content"][0]["image_url"]["url"]
        raw = base64.b64decode(url.split(",", 1)[1])
        with Image.open(io.BytesIO(raw)) as image:
            values.append(image.getpixel((0, 0)))
        labels.append(message["content"][1]["text"])
    assert values == [1, 2, 9]
    assert labels[0] == qwen_vl.BEFORE_LABEL
    assert labels[1] == qwen_vl.BEFORE_LABEL
    assert labels[2] == qwen_vl.NOW_LABEL


def test_actor_messages_can_request_reflection() -> None:
    messages = qwen_vl.build_messages("do it", png(9), [], 3, reflection=True)
    assert "Check:" in messages[0]["content"]
    assert "Expect:" in messages[0]["content"]


def test_the_note_rides_along_with_the_current_screen() -> None:
    messages = qwen_vl.build_messages("do it", png(9), [], 3, "It did not change.")
    assert messages[-1]["content"][1]["text"] == f"{qwen_vl.NOW_LABEL} It did not change."


def test_the_instruction_is_a_content_array_like_every_other_turn() -> None:
    messages = qwen_vl.build_messages("do it", png(9), [], 3)
    assert messages[1]["content"] == [{"type": "text", "text": "do it"}]


# --- coordinates ------------------------------------------------------------


def test_a_point_maps_onto_the_full_resolution_screenshot() -> None:
    assert qwen_vl.to_pixels(CLICK, 1080, 2424)["coordinate"] == [540, 1212]


def test_a_bounding_box_collapses_to_its_centre() -> None:
    out = qwen_vl.to_pixels({"action": "click", "coordinate": [0, 0, 500, 500]}, 100, 200)
    assert out["coordinate"] == [25, 50]


def test_every_coordinate_field_is_converted() -> None:
    out = qwen_vl.to_pixels(
        {
            "action": "drag",
            "start_coordinate": [0, 0],
            "end_coordinate": [1000, 1000],
        },
        100,
        200,
    )
    assert out["start_coordinate"] == [0, 0]
    assert out["end_coordinate"] == [100, 200]


def test_non_coordinate_arguments_pass_through() -> None:
    out = qwen_vl.to_pixels({"action": "long_press", "coordinate": [0, 0], "duration_ms": 2500}, 100, 200)
    assert out["duration_ms"] == 2500


def test_out_of_grid_coordinates_are_rejected() -> None:
    with pytest.raises(ValueError, match="0-1000 grid"):
        qwen_vl.to_pixels({"action": "click", "coordinate": [1400, 10]}, 100, 200)


def test_a_malformed_coordinate_is_rejected() -> None:
    with pytest.raises(ValueError, match="2 or 4 numbers"):
        qwen_vl.to_pixels({"action": "click", "coordinate": [1, 2, 3]}, 100, 200)


def test_a_terminate_missing_its_action_key_is_still_understood() -> None:
    """Observed from the oracle: the Action line said "terminate with status
    fail" and the object carried only the status. `status` belongs to exactly
    one action, so rejecting this aborted a run over an unambiguous reply."""
    parsed = qwen_vl.parse(
        'Action: terminate with status fail\n[tool_call]\n'
        '{"name": "mobile_use", "arguments": {"status": "fail"}}\n[/tool_call]'
    )
    assert parsed.action == "terminate"
    assert parsed.arguments["status"] == "fail"


def test_an_explicit_action_is_never_overridden() -> None:
    parsed = qwen_vl.parse(call({"action": "terminate", "status": "success"}))
    assert parsed.action == "terminate"


def test_a_missing_action_without_a_status_is_still_rejected() -> None:
    """The inference is narrow; it does not guess at arbitrary payloads."""
    with pytest.raises(ValueError, match="unsupported action"):
        qwen_vl.parse(call({"coordinate": [10, 20]}))


def test_a_nonsense_status_is_not_inferred_as_terminate() -> None:
    with pytest.raises(ValueError, match="unsupported action"):
        qwen_vl.parse(call({"status": "maybe"}))
