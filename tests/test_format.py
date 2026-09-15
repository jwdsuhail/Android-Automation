"""How a turn is rendered as a line of text.

Shared by the terminal and the console, so these are the assertions that keep
the two from describing the same tap differently.
"""

from __future__ import annotations

from typing import Any

from android_runner.format import action_said, region, target



def turn(grid: dict[str, Any], pixels: dict[str, Any]) -> dict[str, object]:
    return {"arguments": grid, "pixels": pixels}


def test_region_names_each_corner() -> None:
    assert region([0, 0]) == "top-left"
    assert region([999, 0]) == "top-right"
    assert region([0, 999]) == "bottom-left"
    assert region([999, 999]) == "bottom-right"


def test_region_collapses_the_middle_of_the_screen_to_one_word() -> None:
    assert region([500, 500]) == "center"
    assert region([500, 100]) == "top-center"
    assert region([100, 500]) == "mid-left"


def test_region_clamps_the_far_edge_of_the_grid() -> None:
    # 1000 is inside to_pixels' accepted range, and 1000 * 3 // 1000 is 3.
    assert region([1000, 1000]) == "bottom-right"


def test_region_ignores_values_that_are_not_a_point() -> None:
    assert region([1, 2, 3, 4]) is None
    assert region("nope") is None
    assert region([None, 5]) is None


def test_click_target_carries_the_region_beside_the_numbers() -> None:
    detail = target(
        turn(
            {"action": "click", "coordinate": [782, 61]},
            {"action": "click", "coordinate": [844, 147]},
        )
    )
    assert detail == " 782,61 of 1000 [top-right], px 844,147"


def test_swipe_target_names_both_ends() -> None:
    detail = target(
        turn(
            {"action": "swipe", "coordinate": [500, 800], "coordinate2": [500, 200]},
            {"action": "swipe", "coordinate": [540, 1920], "coordinate2": [540, 480]},
        )
    )
    assert "[bottom-center -> top-center]" in detail
    assert "px 540,1920 -> 540,480" in detail


def test_long_press_keeps_its_duration_after_theregion() -> None:
    detail = target(
        turn(
            {"action": "long_press", "coordinate": [500, 812], "duration_ms": 1500},
            {"action": "long_press", "coordinate": [540, 1968], "duration_ms": 1500},
        )
    )
    assert detail == " 500,812 of 1000 [bottom-center], px 540,1968, 1500ms"


def test_action_said_keeps_the_reason_and_drops_the_tool_call() -> None:
    raw = (
        "Action: Terminate with status fail because test 14 is not visible "
        "in the current contact list.\n"
        "[tool_call]\n"
        '{"name": "mobile_use", "arguments": {"action": "terminate", "status": "fail"}}\n'
        "[/tool_call]"
    )
    assert action_said(raw) == (
        "Terminate with status fail because test 14 is not visible "
        "in the current contact list."
    )


def test_placeless_actions_are_unchanged_by_theregion() -> None:
    assert target(turn({"action": "type", "text": "Test 14"}, {})) == ' "Test 14"'
    assert target(turn({"action": "wait", "time": 5}, {})) == " 5s"
    assert target(turn({"action": "system_button", "button": "Back"}, {})) == " Back"
    assert target(turn({"action": "terminate", "status": "fail"}, {})) == " fail"

