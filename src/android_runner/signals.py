"""Small deterministic checks for screen changes and repeated actions."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageChops, ImageStat

SCREEN_DIFF_SIZE = (128, 128)
# Fixed pixel tiles on the downscaled diff, not a count of tiles per side.
SCREEN_TILE_PIXELS = 16
SCREEN_CHANGE_TILE_THRESHOLD = 0.025


@dataclass(frozen=True)
class ScreenChange:
    mean: float
    tile_max: float

    @property
    def moved(self) -> bool:
        return self.tile_max > SCREEN_CHANGE_TILE_THRESHOLD


def _diff_gray(before: bytes, after: bytes) -> Image.Image:
    with Image.open(io.BytesIO(before)) as left, Image.open(io.BytesIO(after)) as right:
        left_gray = left.convert("L").resize(SCREEN_DIFF_SIZE)
        right_gray = right.convert("L").resize(SCREEN_DIFF_SIZE)
        return ImageChops.difference(left_gray, right_gray)


def screen_change(before: bytes, after: bytes) -> ScreenChange:
    difference = _diff_gray(before, after)
    mean = ImageStat.Stat(difference).mean[0] / 255.0
    width, height = difference.size
    tile = SCREEN_TILE_PIXELS
    tile_max = 0.0
    for top in range(0, height, tile):
        for left in range(0, width, tile):
            crop = difference.crop((left, top, min(left + tile, width), min(top + tile, height)))
            tile_max = max(tile_max, ImageStat.Stat(crop).mean[0] / 255.0)
    return ScreenChange(mean=mean, tile_max=tile_max)


def screen_delta(before: bytes, after: bytes) -> float:
    """Global mean, kept for artifact continuity with older runs."""
    return screen_change(before, after).mean


def action_key(action: dict[str, Any], width: int, height: int) -> str:
    name = str(action.get("action", "?"))

    def bucket(point: Any) -> str | None:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        x = min(31, max(0, int(float(point[0]) * 32 / width)))
        y = min(31, max(0, int(float(point[1]) * 32 / height)))
        return f"{x},{y}"

    start = bucket(action.get("coordinate")) or bucket(action.get("start_coordinate"))
    end = bucket(action.get("coordinate2")) or bucket(action.get("end_coordinate"))
    if start:
        return f"{name}:{start}->{end}" if end else f"{name}:{start}"
    for field in ("text", "button", "status"):
        if action.get(field):
            return f"{name}:{action[field]!r}"
    return name


@dataclass(frozen=True)
class Stuck:
    stuck: bool
    detail: str = ""


def detect_stuck(keys: list[str]) -> Stuck:
    acted = [key for key in keys if not key.startswith("wait")]
    if len(acted) >= 3 and len(set(acted[-3:])) == 1:
        return Stuck(True, f"repeated {acted[-1]!r} three times")
    if len(acted) >= 4:
        tail = acted[-4:]
        if tail[0] != tail[1] and tail == [tail[0], tail[1]] * 2:
            return Stuck(
                True,
                f"oscillated between {tail[0]!r} and {tail[1]!r} twice",
            )
    return Stuck(False)
