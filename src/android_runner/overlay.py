"""Draws the action's target onto a copy of the screen the model acted on.

The log already answers two thirds of "did it tap the right thing": the
narration says what the model believed it was aiming at, and the coordinate
says where the tap actually went. What neither shows is whether those two
agree - that takes opening the screenshot and counting pixels by hand.

Marking the point on the image makes the third part visible at a glance. A
narration reading "the blue play button" over a marker sitting in the message
composer is a grounding failure, and it looks like one immediately.

This module has no model dependencies: it works in device pixels, after
qwen_vl.to_pixels has already mapped the relative grid down.
"""

from __future__ import annotations

import io
import math
from typing import Any

from PIL import Image, ImageDraw

# Fractions of the shorter screen edge, so a marker covers the same share of a
# tablet as of a phone rather than vanishing on one of them.
RADIUS_FRACTION = 0.035
STROKE_FRACTION = 0.004

INK = (255, 32, 32)
HALO = (255, 255, 255)

# Every action that names a place on the screen. `type`, `wait`,
# `system_button`, and `terminate` are absent because they have no target to
# draw, and a marked copy identical to the screenshot is worse than no file.
_SPANS = (("coordinate", "coordinate2"), ("start_coordinate", "end_coordinate"))


def _point(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return int(value[0]), int(value[1])
    except (TypeError, ValueError):
        return None


def endpoints(action: dict[str, Any]) -> tuple[tuple[int, int], tuple[int, int] | None] | None:
    """The action's start and optional end point, in device pixels.

    None when the action has no on-screen target at all.
    """
    for start_key, end_key in _SPANS:
        start = _point(action.get(start_key))
        if start is not None:
            return start, _point(action.get(end_key))
    return None


def _passes(width: int) -> tuple[tuple[tuple[int, int, int], int], ...]:
    """Each shape is drawn twice: a wide white pass, then a narrow red one.

    A single-colour marker disappears against a screen that happens to share
    its colour, and chat apps are full of both red and white.
    """
    return ((HALO, width * 3), (INK, width))


def _ring(draw: ImageDraw.ImageDraw, centre: tuple[int, int], radius: int, width: int) -> None:
    x, y = centre
    for colour, stroke in _passes(width):
        box = (x - radius, y - radius, x + radius, y + radius)
        draw.ellipse(box, outline=colour, width=stroke)


def _cross(draw: ImageDraw.ImageDraw, centre: tuple[int, int], span: int, width: int) -> None:
    x, y = centre
    for colour, stroke in _passes(width):
        draw.line((x - span, y, x + span, y), fill=colour, width=stroke)
        draw.line((x, y - span, x, y + span), fill=colour, width=stroke)


def _arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    head: int,
    width: int,
) -> None:
    """A line from start to end with a head at the end, so a swipe reads
    directionally. Without the head, a scroll up and a scroll down draw the
    same picture."""
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    wings = [
        (
            end[0] - head * math.cos(angle - offset),
            end[1] - head * math.sin(angle - offset),
        )
        for offset in (math.pi / 6, -math.pi / 6)
    ]
    for colour, stroke in _passes(width):
        draw.line((*start, *end), fill=colour, width=stroke)
        for wing in wings:
            draw.line((*end, *wing), fill=colour, width=stroke)


def mark(png: bytes, action: dict[str, Any]) -> bytes | None:
    """Return `png` with the action's target drawn on it, or None.

    None means the action names no place on the screen, so the caller should
    write no file. `action` is in device pixels.
    """
    points = endpoints(action)
    if points is None:
        return None
    start, end = points

    with Image.open(io.BytesIO(png)) as opened:
        image = opened.convert("RGB")

    short = min(image.size)
    radius = max(6, int(short * RADIUS_FRACTION))
    width = max(2, int(short * STROKE_FRACTION))
    draw = ImageDraw.Draw(image)

    if end is not None:
        _arrow(draw, start, end, radius, width)
        _ring(draw, end, radius // 2, width)
    _ring(draw, start, radius, width)
    _cross(draw, start, radius * 2, width)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
