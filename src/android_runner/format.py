"""Rendering a turn record as a line of text.

Shared by the CLI and the console so the two cannot disagree. Reimplementing
the grid arithmetic on the other side of an HTTP boundary is how the terminal
and the browser end up describing the same tap differently.
"""

from __future__ import annotations

from android_runner.qwen_vl import COORD_SCALE


def point(value: object) -> str | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{value[0]},{value[1]}"
    return None


_ROWS = ("top", "mid", "bottom")
_COLUMNS = ("left", "center", "right")


def _third(value: float, names: tuple[str, str, str]) -> str:
    return names[min(2, max(0, int(value * 3 // COORD_SCALE)))]


def region(value: object) -> str | None:
    """Name the ninth of the screen a grid point falls in.

    Derived from the coordinate rather than asked of the model, so it cannot
    disagree with where the tap actually goes. That is the whole point: read
    against the narration it tells you whether the model hit what it named.
    "Tap the send button" over `bottom-left` is a grounding failure that
    otherwise only shows up as a screenshot nobody opened.
    """
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        x, y = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    row, column = _third(y, _ROWS), _third(x, _COLUMNS)
    return "center" if (row, column) == ("mid", "center") else f"{row}-{column}"


def target(turn: dict[str, object]) -> str:
    """The part of the reply the narration leaves out: what it aimed at.

    The sentence says "the plus button"; only the coordinate says *where* the
    model thought that was. Grid and pixels are both shown because a tap that
    lands wrong is either a misread screen (grid) or a bad mapping (pixels),
    and the pair tells them apart without opening turn_NNN.json. The bracketed
    region restates the grid point in words, because "782,61" only reads as
    the top right corner once you have done the arithmetic.
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
        first = point(grid.get(start))
        if first is None:
            continue
        second = point(grid.get(end))
        span = first if second is None else f"{first} -> {second}"
        where = region(grid.get(start))
        if second is not None:
            where = f"{where} -> {region(grid.get(end))}"
        detail = f" {span} of 1000 [{where}], px {point(pixels.get(start))}"
        if second is not None:
            detail += f" -> {point(pixels.get(end))}"
        if action == "long_press":
            # The resolved value, not the model's. Printing what was asked for
            # while the device holds for something else is the failure the
            # hold record exists to end.
            held = turn.get("hold")
            if isinstance(held, dict):
                detail += f", {held.get('ms', '?')}ms"
                source = held.get("source")
                if source and source != "model":
                    detail += f" from {source}"
            else:
                detail += f", {grid.get('duration_ms', '?')}ms"
        return detail
    return ""


def action_said(raw: object) -> str:
    """The Action line from an oracle reply, or the first non-empty line."""
    if not isinstance(raw, str) or not raw.strip():
        return ""
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("action:"):
            return stripped[7:].strip()
        if stripped.startswith(("<", "[")):
            continue
        return stripped
    return ""


def turn_line(turn: dict[str, object]) -> str:
    """The `-> action ... moved` line, without the leading indent.

    The CLI prints this after the narration and the Check/Expect lines; the
    console renders the same string rather than rebuilding it from the record.
    """
    action = turn.get("action", "?")
    moved = turn.get("moved")
    suffix = "" if moved is None else (" moved" if moved else " no-change")
    return f"{action}{target(turn)}{suffix}"
