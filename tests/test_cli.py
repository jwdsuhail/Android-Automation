"""How a turn is rendered for the terminal."""

from __future__ import annotations

from typing import Any

from android_runner.cli import _region, _target


def turn(grid: dict[str, Any], pixels: dict[str, Any]) -> dict[str, object]:
    return {"arguments": grid, "pixels": pixels}


def test_region_names_each_corner() -> None:
    assert _region([0, 0]) == "top-left"
    assert _region([999, 0]) == "top-right"
    assert _region([0, 999]) == "bottom-left"
    assert _region([999, 999]) == "bottom-right"


def test_region_collapses_the_middle_of_the_screen_to_one_word() -> None:
    assert _region([500, 500]) == "center"
    assert _region([500, 100]) == "top-center"
    assert _region([100, 500]) == "mid-left"


def test_region_clamps_the_far_edge_of_the_grid() -> None:
    # 1000 is inside to_pixels' accepted range, and 1000 * 3 // 1000 is 3.
    assert _region([1000, 1000]) == "bottom-right"


def test_region_ignores_values_that_are_not_a_point() -> None:
    assert _region([1, 2, 3, 4]) is None
    assert _region("nope") is None
    assert _region([None, 5]) is None


def test_click_target_carries_the_region_beside_the_numbers() -> None:
    detail = _target(
        turn(
            {"action": "click", "coordinate": [782, 61]},
            {"action": "click", "coordinate": [844, 147]},
        )
    )
    assert detail == " 782,61 of 1000 [top-right], px 844,147"


def test_swipe_target_names_both_ends() -> None:
    detail = _target(
        turn(
            {"action": "swipe", "coordinate": [500, 800], "coordinate2": [500, 200]},
            {"action": "swipe", "coordinate": [540, 1920], "coordinate2": [540, 480]},
        )
    )
    assert "[bottom-center -> top-center]" in detail
    assert "px 540,1920 -> 540,480" in detail


def test_long_press_keeps_its_duration_after_the_region() -> None:
    detail = _target(
        turn(
            {"action": "long_press", "coordinate": [500, 812], "duration_ms": 1500},
            {"action": "long_press", "coordinate": [540, 1968], "duration_ms": 1500},
        )
    )
    assert detail == " 500,812 of 1000 [bottom-center], px 540,1968, 1500ms"


def test_placeless_actions_are_unchanged_by_the_region() -> None:
    assert _target(turn({"action": "type", "text": "Test 14"}, {})) == ' "Test 14"'
    assert _target(turn({"action": "wait", "time": 5}, {})) == " 5s"
    assert _target(turn({"action": "system_button", "button": "Back"}, {})) == " Back"
    assert _target(turn({"action": "terminate", "status": "fail"}, {})) == " fail"
