"""Marking the model's target on the screenshot it acted on."""

from __future__ import annotations

import io

from PIL import Image

from android_runner.overlay import INK, endpoints, mark


def png(size: tuple[int, int] = (200, 400), color: tuple[int, int, int] = (0, 0, 0)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def opened(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as image:
        return image.convert("RGB").copy()


def inked(image: Image.Image) -> set[tuple[int, int]]:
    """Pixels close enough to INK to be marker rather than anti-aliasing."""
    return {
        (x, y)
        for x in range(image.width)
        for y in range(image.height)
        if sum(abs(a - b) for a, b in zip(image.getpixel((x, y)), INK)) < 90
    }


def test_click_marks_the_point_and_leaves_the_size_alone() -> None:
    original = png()
    marked = mark(original, {"action": "click", "coordinate": [100, 200]})
    assert marked is not None
    image = opened(marked)
    assert image.size == opened(original).size

    points = inked(image)
    assert points, "the marker drew nothing"
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    # The ring and crosshair are centred on the target, so their extent is too.
    assert abs((min(xs) + max(xs)) / 2 - 100) <= 2
    assert abs((min(ys) + max(ys)) / 2 - 200) <= 2


def test_long_press_is_marked_like_a_click() -> None:
    marked = mark(
        png(), {"action": "long_press", "coordinate": [100, 200], "duration_ms": 2000}
    )
    assert marked is not None
    assert inked(opened(marked))


def test_swipe_draws_between_both_ends() -> None:
    marked = mark(
        png(),
        {"action": "swipe", "coordinate": [100, 320], "coordinate2": [100, 80]},
    )
    assert marked is not None
    points = inked(opened(marked))
    # Ink on the path between the endpoints is what separates an arrow from
    # two unrelated rings.
    assert any(abs(x - 100) <= 2 and 150 < y < 250 for x, y in points)


def test_drag_uses_its_own_coordinate_keys() -> None:
    assert endpoints(
        {"action": "drag", "start_coordinate": [10, 20], "end_coordinate": [30, 40]}
    ) == ((10, 20), (30, 40))


def test_actions_without_a_place_on_screen_are_not_marked() -> None:
    for action in (
        {"action": "type", "text": "Test 14"},
        {"action": "wait", "time": 5},
        {"action": "system_button", "button": "Back"},
        {"action": "terminate", "status": "success"},
    ):
        assert endpoints(action) is None
        assert mark(png(), action) is None


def test_a_target_on_the_edge_does_not_raise() -> None:
    # to_pixels floors the grid, so a point can land on the last row or column
    # and the marker is then mostly outside the image.
    assert mark(png(), {"action": "click", "coordinate": [199, 399]}) is not None
    assert mark(png(), {"action": "click", "coordinate": [0, 0]}) is not None


def test_malformed_coordinates_are_not_marked() -> None:
    assert mark(png(), {"action": "click", "coordinate": [1, 2, 3, 4]}) is None
    assert mark(png(), {"action": "click", "coordinate": "nope"}) is None
