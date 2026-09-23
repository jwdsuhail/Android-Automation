"""How long a long_press holds, and who decided it.

A hold that is shorter than the device's own long-press threshold is delivered
as a tap. Nothing in Android reports that: the app just does the wrong thing,
and the run record still shows the number that was asked for. This module keeps
the decision and the arithmetic in one place so every adjustment between what
was asked for and what the device does ends up written down.

Three sources can name a duration, in falling order of authority: the
instruction you wrote, the value the model emitted, and the configured default.
The instruction wins because it is the one of the three that is certainly what
you meant.

Model-agnostic and device-agnostic. Pure functions over numbers and strings;
nothing here runs a command or opens a socket.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Sequence

# Android's ViewConfiguration.getLongPressTimeout default, used when the device
# will not say what its own threshold is.
DEFAULT_FLOOR_MS = 500

# `adb shell input swipe` blocks for the whole hold, so a hold longer than the
# ADB timeout kills the call instead of pressing anything. The margin covers
# process start and the round trip either side of the press itself.
ADB_MARGIN_S = 2.0

# Below this, a value is not a press duration in milliseconds under any Android
# configuration - it is under a single frame. Seconds is the only unit the
# model could have meant.
SECONDS_CEILING_MS = 50

# The instruction's verbs, split by whether they take a duration that belongs
# to a hold. "press" counts as a hold verb only because the nearest-verb rule
# below protects it: "press Back, then wait for 3 seconds" gives the 3 seconds
# to `wait`, which is nearer, so a bare "press ... for 3 seconds" really has no
# other reading.
_HOLD_VERBS = (
    "long press",
    "press and hold",
    "tap and hold",
    "click and hold",
    "hold",
    "press",
)
_OTHER_VERBS = ("wait", "pause", "sleep", "click", "tap", "swipe", "drag", "scroll", "type")

# Longest first, so "long press" is not read as "press", and a space in a verb
# also matches a hyphen. The trailing \w* takes the inflections: pressing,
# held over to "holds", "tapped", "waiting".
_VERB = re.compile(
    r"\b("
    + "|".join(
        re.escape(verb).replace(r"\ ", r"[\s\-]+")
        for verb in sorted(_HOLD_VERBS + _OTHER_VERBS, key=len, reverse=True)
    )
    + r")\w*",
    re.IGNORECASE,
)

# Alternation order is load-bearing: `s` last, or "3 seconds" matches as "3 s"
# with "econds" left over.
_DURATION = re.compile(
    r"\bfor\s+(\d+(?:\.\d+)?)\s*"
    r"(milliseconds?|millisecs?|msecs?|ms|seconds?|secs?|s)\b",
    re.IGNORECASE,
)

_MILLISECOND_UNITS = ("ms", "msec", "msecs", "millisec", "millisecs", "millisecond", "milliseconds")


def ceiling_ms(adb_timeout_s: float) -> int:
    """The longest hold that can finish before ADB gives up on the call."""
    return max(1, int((adb_timeout_s - ADB_MARGIN_S) * 1000))


def _trim(value: float) -> str:
    """3.0 -> "3", 2.5 -> "2.5". Notes are read by people."""
    return f"{value:g}"


@dataclass(frozen=True)
class Hold:
    """The duration that will be executed, and the trail that led to it."""

    ms: int
    source: str  # "instruction" | "model" | "default"
    requested_ms: int | None
    model_ms: int | None
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ms": self.ms,
            "source": self.source,
            "requested_ms": self.requested_ms,
            "model_ms": self.model_ms,
            "notes": list(self.notes),
        }


def _stated(text: str) -> list[int]:
    """Every "for N seconds" in the text that belongs to a hold verb.

    The rule is the nearest preceding verb, not the nearest hold verb. A
    pattern that only looked for a hold verb somewhere before the duration
    reads "long press the icon, then wait for 10 seconds" as a ten second
    hold, because "long press" is indeed somewhere before it.
    """
    found: list[int] = []
    for match in _DURATION.finditer(text):
        # The capture group holds the verb's stem, so "long-pressing" arrives
        # here as "long-press" and only needs its hyphen folded back to a space.
        verbs = _VERB.findall(text[: match.start()])
        if not verbs:
            continue
        nearest = " ".join(verbs[-1].lower().replace("-", " ").split())
        if nearest not in _HOLD_VERBS:
            continue
        value = float(match.group(1))
        unit = match.group(2).lower()
        found.append(int(round(value if unit in _MILLISECOND_UNITS else value * 1000)))
    return found


def from_instruction(text: str) -> tuple[int | None, tuple[str, ...]]:
    """The hold time the instruction states, if it states exactly one.

    Two different stated durations cannot both be enforced from one
    instruction-level slot, so neither is: the function returns nothing and
    says why, and the model chooses per turn as it otherwise would. This is
    the same refusal as ui_tree.find handing back every match - a duration
    nobody chose is worse than no duration.

    Two *identical* durations are not a conflict and are enforced.
    """
    stated = _stated(text)
    distinct = sorted(set(stated))
    if not distinct:
        return None, ()
    if len(distinct) > 1:
        spelled = ", ".join(f"{value}ms" for value in distinct)
        return None, (
            f"instruction states {len(distinct)} different hold times ({spelled}); "
            "leaving the choice to the model",
        )
    return distinct[0], (f"instruction asked for {_trim(distinct[0] / 1000)}s",)


def _number(raw: object, key: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ValueError(f"{key} must be a number, got {raw!r}")
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{key} must be a number, got {raw!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{key} must be a positive number, got {raw!r}")
    return value


def from_arguments(arguments: dict[str, Any]) -> tuple[int | None, tuple[str, ...]]:
    """The duration the model emitted, in milliseconds, with its unit repaired.

    Qwen's own mobile_use schema spells this `time`, in seconds, shared with
    the `wait` action; this prompt asks for `duration_ms`, in milliseconds, on
    long_press alone. The model's prior therefore points at seconds, and
    `duration_ms: 3` meaning three seconds is the expected mistake rather than
    an exotic one.

    Such a value is repaired rather than refused, and the repair is recorded.
    Refusing is the stricter reading and it is what the grounding literature
    recommends, but it ends an otherwise recoverable run over a unit; the note
    means the same mistakes can be counted and the prompt tightened instead.

    A value that is not a positive number is a different matter and raises, so
    the runner can file it as a parse_error - the model's fault - rather than
    letting it surface from inside device.execute as infrastructure.
    """
    for key in ("duration_ms", "time"):
        if key in arguments:
            raw = arguments[key]
            break
    else:
        return None, ()

    notes: list[str] = []
    value = _number(raw, key)
    if isinstance(raw, str):
        notes.append(f"model sent {key} as the string {raw!r}")

    if key == "time":
        ms = int(round(value * 1000))
        notes.append(f"model used `time`, Qwen's seconds key: {_trim(value)}s read as {ms}ms")
        return ms, tuple(notes)

    if value < SECONDS_CEILING_MS:
        ms = int(round(value * 1000))
        notes.append(
            f"model sent duration_ms {_trim(value)}, under {SECONDS_CEILING_MS}ms; "
            f"read as seconds and repaired to {ms}ms"
        )
        return ms, tuple(notes)
    return int(round(value)), tuple(notes)


def resolve(
    instruction_ms: int | None,
    model_ms: int | None,
    default_ms: int,
    floor_ms: int,
    ceiling_ms_: int,
    notes: Sequence[str] = (),
) -> Hold:
    """Pick the duration, apply the device's bounds, and record every change.

    The ceiling is applied after the floor so a device whose long-press
    threshold somehow exceeds the ADB timeout still gets a hold that returns,
    rather than one that kills the call.
    """
    trail = list(notes)
    if instruction_ms is not None:
        requested, source = instruction_ms, "instruction"
        if model_ms is not None and model_ms != instruction_ms:
            trail.append(f"model asked for {model_ms}ms, overridden by the instruction")
    elif model_ms is not None:
        requested, source = model_ms, "model"
    else:
        requested, source = default_ms, "default"

    ms = requested
    if ms < floor_ms:
        trail.append(
            f"{ms}ms is under this device's {floor_ms}ms long-press threshold, "
            f"where a hold is delivered as a tap; raised to {floor_ms}ms"
        )
        ms = floor_ms
    if ms > ceiling_ms_:
        trail.append(
            f"{ms}ms is over the {ceiling_ms_}ms ADB call ceiling; lowered to {ceiling_ms_}ms"
        )
        ms = ceiling_ms_
    return Hold(
        ms=ms,
        source=source,
        requested_ms=requested,
        model_ms=model_ms,
        notes=tuple(trail),
    )
