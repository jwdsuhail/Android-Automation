"""Screen-change metrics and stuck detection."""

from __future__ import annotations

import io

from PIL import Image

from android_runner.signals import (
    SCREEN_CHANGE_TILE_THRESHOLD,
    SCREEN_DIFF_SIZE,
    SCREEN_TILE_PIXELS,
    action_key,
    detect_stuck,
    screen_change,
    screen_delta,
    wait_until_settled,
)


class Clock:
    """Time only passes when the code under test asks to wait. A real clock
    would make the timeout test depend on how fast the machine ran it."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def read(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def frames(*images: bytes):
    """Hands back each frame in turn and then repeats the last one forever."""
    remaining = list(images)
    taken: list[bytes] = []

    def shoot() -> bytes:
        image = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        taken.append(image)
        return image

    return shoot, taken


def png_gray(size: tuple[int, int], color: int, paint: tuple[int, int, int, int, int] | None = None) -> bytes:
    image = Image.new("L", size, color=color)
    if paint is not None:
        left, top, right, bottom, value = paint
        for y in range(top, bottom):
            for x in range(left, right):
                image.putpixel((x, y), value)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_identical_images_are_unmoved() -> None:
    image = png_gray(SCREEN_DIFF_SIZE, 0)
    change = screen_change(image, image)
    assert change.mean == 0
    assert change.tile_max == 0
    assert change.moved is False


def test_full_frame_change_moves_both_metrics() -> None:
    before = png_gray(SCREEN_DIFF_SIZE, 0)
    after = png_gray(SCREEN_DIFF_SIZE, 255)
    change = screen_change(before, after)
    assert change.mean == 1.0
    assert change.tile_max == 1.0
    assert change.moved is True
    assert screen_delta(before, after) == change.mean


def test_localized_tile_is_moved_while_global_mean_stays_small() -> None:
    before = png_gray(SCREEN_DIFF_SIZE, 0)
    after = png_gray(SCREEN_DIFF_SIZE, 0, paint=(0, 0, SCREEN_TILE_PIXELS, SCREEN_TILE_PIXELS, 50))
    change = screen_change(before, after)
    assert change.mean < 0.01
    assert change.tile_max > SCREEN_CHANGE_TILE_THRESHOLD
    assert change.moved is True


def test_tile_threshold_boundary() -> None:
    below_gray = 6  # 6/255 = 0.0235
    above_gray = 7  # 7/255 = 0.0275
    before = png_gray(SCREEN_DIFF_SIZE, 0)
    below = screen_change(
        before,
        png_gray(SCREEN_DIFF_SIZE, 0, paint=(0, 0, SCREEN_TILE_PIXELS, SCREEN_TILE_PIXELS, below_gray)),
    )
    above = screen_change(
        before,
        png_gray(SCREEN_DIFF_SIZE, 0, paint=(0, 0, SCREEN_TILE_PIXELS, SCREEN_TILE_PIXELS, above_gray)),
    )
    assert below.tile_max < SCREEN_CHANGE_TILE_THRESHOLD
    assert below.moved is False
    assert above.tile_max > SCREEN_CHANGE_TILE_THRESHOLD
    assert above.moved is True


def test_action_key_buckets_coordinates() -> None:
    key = action_key({"action": "click", "coordinate": [0, 0]}, 100, 200)
    assert key == "click:0,0"


def test_triple_repeat_is_stuck() -> None:
    keys = ["click:1,1", "click:1,1", "click:1,1"]
    stuck = detect_stuck(keys)
    assert stuck.stuck is True
    assert "click:1,1" in stuck.detail


def test_abab_oscillation_is_stuck() -> None:
    keys = ["click:1,1", "click:2,2", "click:1,1", "click:2,2"]
    stuck = detect_stuck(keys)
    assert stuck.stuck is True
    assert "oscillated" in stuck.detail


def test_wait_keys_are_ignored_by_stuck_detection() -> None:
    keys = ["wait", "wait", "wait", "click:1,1"]
    assert detect_stuck(keys).stuck is False


def test_the_wait_ends_on_the_first_pair_of_matching_frames() -> None:
    drawing = png_gray(SCREEN_DIFF_SIZE, 0)
    drawn = png_gray(SCREEN_DIFF_SIZE, 255)
    shoot, taken = frames(drawing, drawn, drawn)
    clock = Clock()
    outcome = wait_until_settled(
        shoot, timeout_s=3, poll_s=0.25, clock=clock.read, sleep=clock.sleep
    )
    assert outcome.settled is True
    assert outcome.frames == 3
    assert outcome.png == drawn  # the settled screen, not the one mid-draw
    assert outcome.ms == 500.0
    assert len(taken) == 3


def test_a_screen_that_never_stops_still_hands_back_the_newest_frame() -> None:
    """Running out of time is a normal exit. An animation that loops forever
    must not stall the turn, and the model still needs something to look at."""
    dark = png_gray(SCREEN_DIFF_SIZE, 0)
    light = png_gray(SCREEN_DIFF_SIZE, 255)
    flickering = [dark, light] * 20
    taken: list[bytes] = []

    def shoot() -> bytes:
        taken.append(flickering[len(taken) % 2])
        return taken[-1]

    clock = Clock()
    outcome = wait_until_settled(
        shoot, timeout_s=1, poll_s=0.25, clock=clock.read, sleep=clock.sleep
    )
    assert outcome.settled is False
    assert outcome.ms == 1000.0
    assert outcome.png == taken[-1]
    assert clock.slept == [0.25] * 4  # polled, not spun


def test_a_zero_cap_takes_one_screenshot_and_never_sleeps() -> None:
    """The escape hatch has to cost exactly what the old single capture did."""
    image = png_gray(SCREEN_DIFF_SIZE, 0)
    shoot, taken = frames(image)
    clock = Clock()
    outcome = wait_until_settled(
        shoot, timeout_s=0, clock=clock.read, sleep=clock.sleep
    )
    assert outcome.frames == 1
    assert outcome.ms == 0.0
    assert outcome.settled is False
    assert len(taken) == 1
    assert clock.slept == []
