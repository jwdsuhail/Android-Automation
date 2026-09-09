"""Everything specific to Qwen3-VL: prompt, messages, parsing, replay, coords.

The model answers on a relative 0-1000 grid and calls one `mobile_use` tool.
Nothing here talks to ADB or performs HTTP; the runner supplies screenshots and
executes the pixel-space action this module returns.

This file is named for the model on purpose. A second model means a second file
like it, not an abstraction layered over this one.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any

# The resolution Qwen-family GUI models are trained at. Sending more pixels
# than this decreases grounding accuracy, so it is a default rather than a
# tuning knob. Applied by wire.downscale_png.
MAX_PIXELS = 1003520

# The model's coordinate space, not the device's.
COORD_SCALE = 1000

SYSTEM_PROMPT = """You are a mobile GUI agent. The screen's resolution is 1000x1000.
Coordinates you output are relative on that 0-1000 grid, not device pixels.
Click the center of the target, not the edge.

# Tools
You may call mobile_use. For each call, return a Thought line, an Action line, then a json object within <tool_call> tags:

Thought: What you see on the screen, what you are looking for, and why you chose this element. If the target is not visible, say so and swipe to look for it.
Action: One short sentence naming what you do now.
<tool_call>
{"name": "mobile_use", "arguments": <args-json-object>}
</tool_call>

## Action space
{"action": "click", "coordinate": [x, y]}
{"action": "long_press", "coordinate": [x, y], "duration_ms": 1000}
{"action": "type", "text": "..."}
{"action": "swipe", "coordinate": [x, y], "coordinate2": [x2, y2]}
{"action": "drag", "start_coordinate": [x, y], "end_coordinate": [x2, y2]}
{"action": "system_button", "button": "Back|Home|Menu|Enter"}
{"action": "wait", "time": 5}
{"action": "terminate", "status": "success|fail"}

To open an app, tap its icon on the screen. If you cannot see the icon, press the Home button and look again, or swipe to reach the rest of the app list. There is no command that launches an app by name.

A control that says hold, press and hold, or hold to confirm needs long_press, not click. A tap on such a control does nothing at all. Set duration_ms to about 2000-3000 for a hold-to-confirm button, and about 800 for a context menu. If the instruction tells you how long to hold, use that.

For swipe, coordinate is where the finger starts and coordinate2 is where it ends. To scroll a list down the screen, start low and end high.

If the screen is busy - loading, generating, uploading, compressing, saving - wait instead of tapping. The signs are a progress bar or a spinner, a percentage, a greyed-out button, or text such as Generating, Uploading, or Processing. Set time to the seconds you expect it to need: about 3 for a screen that is loading, 10 or more for an upload or an image being generated. After a wait you are told what the screen did. If an indicator is still showing, wait again with a longer time. If the indicator is gone and nothing else changed, the operation has finished or failed, so act instead of waiting again.

Do not terminate with status success unless the requested task is complete on the screen in front of you.
""".strip()

# Thinking is switched off, not deleted. This variant is *derived* from the
# prompt above rather than written out again: edit SYSTEM_PROMPT and both
# follow. A second hand-maintained copy would drift, and the drift would be
# silent. `_assert_derived` below fails loudly if an edit breaks the anchors.
_THOUGHT_REQUEST = "return a Thought line, an Action line, then a json object"
_THOUGHT_LINE = (
    "Thought: What you see on the screen, what you are looking for, and why you"
    " chose this element. If the target is not visible, say so and swipe to"
    " look for it.\n"
)

_NO_THINK_REQUEST = "return an Action line, then a json object"
_ACTION_LINE_TEXT = "Action: One short sentence naming what you do now.\n"
_GO_STRAIGHT_ACTION = "Go straight to the Action line."
_CHECK_LINE = (
    "Check: Say if your last action did what you expected. Write \"first step\" "
    "on the first turn. On later turns, compare the current screen to the previous "
    "Expect. If the last action failed, correct it or try another method rather "
    "than terminate.\n"
)
_EXPECT_LINE = (
    "Expect: Say what the screen must show after the action you choose now.\n"
)

NO_THINK_SYSTEM_PROMPT = (
    SYSTEM_PROMPT.replace(_THOUGHT_REQUEST, _NO_THINK_REQUEST)
    .replace(_THOUGHT_LINE, "")
    .replace(
        "</tool_call>\n",
        "</tool_call>\n\nDo not explain your reasoning. Do not write a Thought"
        f" line. {_GO_STRAIGHT_ACTION}\n",
    )
)


def _assert_derived() -> None:
    """Both anchors must still match, or the no-think prompt silently is not one."""
    for anchor in (_THOUGHT_REQUEST, _THOUGHT_LINE, "</tool_call>\n"):
        if anchor not in SYSTEM_PROMPT:
            raise AssertionError(
                f"NO_THINK_SYSTEM_PROMPT derivation lost its anchor: {anchor[:40]!r}"
            )
    for anchor in (_NO_THINK_REQUEST, _ACTION_LINE_TEXT, _GO_STRAIGHT_ACTION):
        if anchor not in NO_THINK_SYSTEM_PROMPT:
            raise AssertionError(
                f"reflection prompt derivation lost its no-think anchor: {anchor[:40]!r}"
            )
    if _ACTION_LINE_TEXT not in SYSTEM_PROMPT:
        raise AssertionError("reflection prompt derivation lost the Action line")


_assert_derived()


def system_prompt(thinking: bool, reflection: bool = False) -> str:
    """Return the actor or oracle prompt.

    Thinking and reflection are independent: reflection is three short labelled
    lines, not a reasoning block. The independent checker always calls this
    with reflection off.
    """
    prompt = SYSTEM_PROMPT if thinking else NO_THINK_SYSTEM_PROMPT
    if not reflection:
        return prompt
    if thinking:
        return prompt.replace(
            _THOUGHT_REQUEST,
            "return a Thought line, a Check line, an Expect line, an Action "
            "line, then a json object",
        ).replace(_THOUGHT_LINE, _THOUGHT_LINE + _CHECK_LINE + _EXPECT_LINE)
    return (
        prompt.replace(
            _NO_THINK_REQUEST,
            "return a Check line, an Expect line, an Action line, then a json object",
        )
        .replace(_ACTION_LINE_TEXT, _CHECK_LINE + _EXPECT_LINE + _ACTION_LINE_TEXT)
        .replace(_GO_STRAIGHT_ACTION, "Go straight to the Check line.")
    )


# `answer` is deliberately absent: device.execute has no branch for it and
# would raise. `drag` is present because device.execute does handle it.
ALLOWED_ACTIONS: frozenset[str] = frozenset(
    (
        "click",
        "long_press",
        "type",
        "swipe",
        "drag",
        "system_button",
        "wait",
        "terminate",
    )
)

_COORDINATE_KEYS = ("coordinate", "coordinate2", "start_coordinate", "end_coordinate")

# Every spelling of the block, in one pass so the earliest one in the text
# wins. The bracket form exists because Ollama 500s on <tool_call> tags in
# prompt text, so the request may go out bracketed (wire.escape_tool_call_tags)
# and come back the same way. The fence is there because a model asked for one
# block often adds markdown.
_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*(?P<tagged>.*?)\s*</tool_call>"
    r"|\[tool_call\]\s*(?P<bracketed>.*?)\s*\[/tool_call\]"
    r"|```(?:tool_call|json)?\s*(?P<fenced>\{.*?\})\s*```",
    re.DOTALL,
)

_LEADING_THINK_RE = re.compile(
    r"^\s*<(think|thinking)>\s*(.*?)\s*</\1>\s*", re.DOTALL | re.IGNORECASE
)
_THINK_BLOCK_RE = re.compile(
    r"<(think|thinking)>\s*(.*?)\s*</\1>", re.DOTALL | re.IGNORECASE
)

# The lookahead lists every spelling of the block the reply may come back in.
_THOUGHT_LINE_RE = re.compile(
    r"^Thought:\s*(.+?)(?=\n(?:Check:|Expect:|Action:|<tool_call>|\[tool_call\]|```)|\Z)",
    re.DOTALL | re.MULTILINE,
)

# Check, Expect, and Action are one sentence each, so they stop at the newline.
# The Thought above them may wrap, so that one runs until the next label or
# the block.
#
# `[ \t]` and not `\s`: `\s` matches a newline, so a bare `Action:` label with
# its content on the following line used to capture that line instead - and
# when the next line was the tool call, the JSON became the narration. A model
# told to go straight to the Action line writes exactly that shape.
_CHECK_LINE_RE = re.compile(r"^Check:[ \t]*(.+?)[ \t]*$", re.MULTILINE)
_EXPECT_LINE_RE = re.compile(r"^Expect:[ \t]*(.+?)[ \t]*$", re.MULTILINE)
_ACTION_LINE_RE = re.compile(r"^Action:[ \t]*(.+?)[ \t]*$", re.MULTILINE)

_CLOSE_TAGS = ("</think>", "</thinking>")


@dataclass(frozen=True)
class ParsedAction:
    """`thinking` is why the model acted; `narration` is what it says it did.

    They are separate because a console that shows only one of them shows the
    wrong one: the narration restates the action, while the reasoning is the
    part that explains a wrong tap. `check` and `expectation` are the short
    reflection lines: whether the last action matched its Expect, and what
    the current action must produce. They are not a reasoning block.
    """

    arguments: dict[str, Any]
    thinking: str | None = None
    narration: str | None = None
    check: str | None = None
    expectation: str | None = None

    @property
    def action(self) -> str:
        return str(self.arguments.get("action", ""))


def _split_dangling_close(text: str) -> tuple[str, str | None]:
    """Split on a closing think tag that was never opened in the output.

    Ollama's chat template for reasoning models prefills the opening <think>
    into the prompt, so the completion carries only the closing tag
    (ollama/ollama#12593). The paired-tag regexes cannot see that, and without
    this the reasoning stays in the action text - where the parser then splits
    on an "Action:" line the model wrote while it was still thinking.
    """
    lowered = text.lower()
    for tag in _CLOSE_TAGS:
        index = lowered.find(tag)
        if index == -1:
            continue
        head = text[:index]
        if "<think" in head.lower():
            continue  # Properly paired; the block regexes handle it.
        if _TOOL_CALL_RE.search(head):
            # The head holds the action, so it is output and not reasoning.
            # Splitting here would discard the tool call and turn a good reply
            # into "no tool_call block", which is the wrong diagnosis.
            continue
        return text[index + len(tag) :].lstrip(), head.strip() or None
    return text, None


def extract_reasoning(
    raw: str, reasoning_content: str | None = None
) -> tuple[str, str | None]:
    """Split reasoning from action text. Does not parse the action.

    Accepts a leading <think> block, a closing tag whose opener the server put
    in the prompt, and/or a separate reasoning field from the HTTP response.
    Reasoning is returned separately so it cannot leak into the action parser.
    """
    text, inline = _split_dangling_close(raw or "")

    leading = _LEADING_THINK_RE.match(text)
    if leading:
        inline = inline or leading.group(2).strip() or None
        text = text[leading.end() :]

    def _strip(match: re.Match[str]) -> str:
        nonlocal inline
        captured = match.group(2).strip()
        if inline is None and captured:
            inline = captured
        return ""

    cleaned = _THINK_BLOCK_RE.sub(_strip, text).strip()

    if reasoning_content is not None and reasoning_content.strip():
        return cleaned, reasoning_content.strip()
    return cleaned, inline


def find_tool_call(text: str) -> str | None:
    """Return the first tool-call JSON body - tagged, bracketed, or fenced."""
    match = _TOOL_CALL_RE.search(text)
    if not match:
        return None
    for group in ("tagged", "bracketed", "fenced"):
        body = match.group(group)
        if body is not None:
            return body.strip()
    return None


def _lead_in_text(text: str) -> str | None:
    """Prose before the tool-call block, for models that omit `Thought:`.

    The prompt asks for a labelled line, but a model routinely writes the
    sentence bare - "Tap on the plus icon in the top right corner." That text
    is its stated reason, so it is worth recovering rather than discarding
    because the label is missing.
    """
    match = _TOOL_CALL_RE.search(text)
    if not match:
        return None
    return text[: match.start()].strip() or None


def _labelled(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1).strip() or None


def _infer_missing_action(arguments: dict[str, Any]) -> dict[str, Any]:
    """Fill in `action: terminate` when only `status` came back.

    Observed from the verification oracle: it writes
    `Action: terminate with status fail` and then emits
    `{"name": "mobile_use", "arguments": {"status": "fail"}}` - the label
    already said "terminate", so the key was dropped from the object. Rejecting
    that aborted a run over a reply whose meaning was not in doubt.

    Narrow on purpose: `status` belongs to exactly one action in ALLOWED_ACTIONS,
    so there is nothing else this could be. Nothing else is inferred.
    """
    if "action" in arguments:
        return arguments
    if str(arguments.get("status", "")).lower() in ("success", "fail"):
        return {"action": "terminate", **arguments}
    return arguments


def parse(raw: str, reasoning: str | None = None) -> ParsedAction:
    cleaned, extracted = extract_reasoning(raw, reasoning)
    thought = _labelled(_THOUGHT_LINE_RE, cleaned)
    check = _labelled(_CHECK_LINE_RE, cleaned)
    expectation = _labelled(_EXPECT_LINE_RE, cleaned)
    action_match = _ACTION_LINE_RE.search(cleaned)
    narration = (action_match.group(1).strip() or None) if action_match else None
    thinking = reasoning if reasoning else extracted or thought

    if thinking is None and action_match is not None:
        # A server that routes thinking to its own channel eats the `Thought:`
        # label but leaves the prose above the Action line. That prose is the
        # reasoning, label or no label. Labelled Check/Expect lines are not
        # reasoning; strip them before treating the remainder as thought.
        prefix = cleaned[: action_match.start()]
        stripped = _CHECK_LINE_RE.sub("", prefix)
        stripped = _EXPECT_LINE_RE.sub("", stripped).strip()
        if stripped and not stripped.lower().startswith(("check:", "expect:")):
            thinking = stripped

    if narration is None:
        # No Action label. A model that writes one bare sentence is narrating
        # the tap, not reasoning about it, so it belongs on the narration line
        # - dropping it left every step of a real run with nothing to show.
        lead = _lead_in_text(cleaned)
        # A bare `Action:` with nothing after it is a label, not a sentence. It
        # reaches here when the model put the content on the next line, and
        # passing it through would print "Action:" as the step's description.
        if lead and lead.strip().lower() in ("action:", "thought:", "check:", "expect:"):
            lead = None
        if (
            lead
            and lead != thinking
            and not lead.lower().startswith(("thought:", "check:", "expect:"))
            and _CHECK_LINE_RE.search(lead) is None
            and _EXPECT_LINE_RE.search(lead) is None
        ):
            narration = lead

    if narration and narration.lower().startswith("thought:"):
        # Some no-think replies nest the old label inside the Action line:
        # `Action: Thought: Tap it`. Keep the statement once, in the field
        # named by its label.
        embedded = narration[len("thought:") :].strip()
        thinking = thinking or embedded or None
        narration = None
    if narration and narration.lower().startswith("expect:"):
        embedded = narration[len("expect:") :].strip()
        expectation = expectation or embedded or None
        narration = None
    if narration and narration.lower().startswith("check:"):
        embedded = narration[len("check:") :].strip()
        check = check or embedded or None
        narration = None
    if narration and thinking:
        if " ".join(narration.split()).casefold() == " ".join(thinking.split()).casefold():
            # Identical labelled Thought/Action lines are one statement, not
            # two useful record fields.
            narration = None

    body = find_tool_call(cleaned)
    if body is None:
        raise ValueError("no tool_call block in model output")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid tool_call JSON: {exc}") from exc
    if payload.get("name") != "mobile_use":
        raise ValueError(f"expected tool name mobile_use, got {payload.get('name')!r}")
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        raise ValueError("tool_call.arguments must be an object")
    arguments = _infer_missing_action(dict(arguments))
    if arguments.get("action") not in ALLOWED_ACTIONS:
        raise ValueError(f"unsupported action {arguments.get('action')!r}")
    return ParsedAction(
        arguments=arguments,
        thinking=thinking,
        narration=narration,
        check=check,
        expectation=expectation,
    )


def replay(raw: str) -> str:
    """What a prior turn contributes to the *next* prompt.

    A later turn needs the previous Expect so it can Check it, plus what was
    done. Reasoning is the larger half of a reply - measured at 58% - and the
    Check line is a judgment of the step before that, so both are dropped.
    The full reply is still written to disk, parsed, and displayed.
    """
    cleaned, _ = extract_reasoning(raw)
    expect = _EXPECT_LINE_RE.search(cleaned)
    if expect is not None:
        return cleaned[expect.start() :].strip()
    match = _ACTION_LINE_RE.search(cleaned)
    if match is not None:
        return cleaned[match.start() :].strip()
    thought = _THOUGHT_LINE_RE.search(cleaned)
    return cleaned[thought.end() :].lstrip() if thought else cleaned


def _xy(coords: Any, key: str) -> tuple[float, float]:
    """Accept a point or a bounding box; a box collapses to its centre."""
    if not isinstance(coords, (list, tuple)):
        raise ValueError(f"{key} must be [x, y] or [x1, y1, x2, y2]")
    if len(coords) == 2:
        return float(coords[0]), float(coords[1])
    if len(coords) == 4:
        x1, y1, x2, y2 = (float(value) for value in coords)
        return (x1 + x2) / 2, (y1 + y2) / 2
    raise ValueError(f"{key} must be 2 or 4 numbers, got {len(coords)}")


def to_pixels(arguments: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    """Copy arguments with every coordinate field mapped to device pixels.

    Scaled against the real screenshot size, not the downscaled copy sent to
    the model: the 0-1000 grid is relative, so it does not move when the image
    does.
    """
    out = dict(arguments)
    for key in _COORDINATE_KEYS:
        if key not in out:
            continue
        x, y = _xy(out[key], key)
        if not 0 <= x <= COORD_SCALE or not 0 <= y <= COORD_SCALE:
            raise ValueError(f"{key} must be within the 0-{COORD_SCALE} grid")
        out[key] = [
            int(x / COORD_SCALE * width),
            int(y / COORD_SCALE * height),
        ]
    return out


# The model sees one screen it acted on and one it is looking at now, and
# cannot relate them without being told which is which. These labels anchor it.
BEFORE_LABEL = "The screen you were looking at when you chose the action below."
NOW_LABEL = "The screen now."


def _image_message(png: bytes, label: str) -> dict[str, Any]:
    """The image part stays first: _is_image_message identifies a screenshot
    turn by its first content part, so drop_old_images drops the whole message,
    caption included."""
    encoded = base64.b64encode(png).decode("ascii")
    return {
        "role": "user",
        "content": [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}"},
            },
            {"type": "text", "text": label},
        ],
    }


def _is_image_message(message: dict[str, Any]) -> bool:
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if not isinstance(content, list) or not content:
        return False
    first = content[0]
    return isinstance(first, dict) and first.get("type") == "image_url"


def drop_old_images(
    messages: list[dict[str, Any]], history_n: int
) -> list[dict[str, Any]]:
    """Keep every assistant turn; keep only the last history_n images.

    Dropping a screenshot and its assistant turn together would erase the
    model's record of what it already tried, which is what makes an agent
    retry the same failing tap. The action trail is cheap text and stays;
    only the images, which are not, are capped.

    history_n is a total image count *including* the current screenshot, not a
    number of prior turns.
    """
    if history_n < 1:
        history_n = 1
    image_indices = [i for i, message in enumerate(messages) if _is_image_message(message)]
    drop = set(image_indices[:-history_n])
    return [message for i, message in enumerate(messages) if i not in drop]


def build_messages(
    instruction: str,
    png: bytes,
    history: list[tuple[bytes, str]] | None,
    history_n: int,
    note: str | None = None,
    *,
    thinking: bool = False,
    reflection: bool = False,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt(thinking, reflection)},
        {"role": "user", "content": [{"type": "text", "text": instruction}]},
    ]
    for old_png, assistant_text in history or []:
        messages.append(_image_message(old_png, BEFORE_LABEL))
        messages.append({"role": "assistant", "content": assistant_text})
    messages.append(
        _image_message(png, f"{NOW_LABEL} {note}".strip() if note else NOW_LABEL)
    )
    return drop_old_images(messages, history_n)
