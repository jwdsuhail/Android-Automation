"""Who decides how long a hold lasts, and what gets written down when it changes.

The unit mismatch these cover is not hypothetical: Qwen's own mobile_use schema
spells this parameter `time`, in seconds, so `duration_ms: 3` meaning three
seconds is the mistake the model is primed to make.
"""

from __future__ import annotations

import pytest

from android_runner.hold import (
    DEFAULT_FLOOR_MS,
    SECONDS_CEILING_MS,
    ceiling_ms,
    from_arguments,
    from_instruction,
    resolve,
)

FLOOR = 400  # what the emulator actually reports on SDK 35
CEILING = ceiling_ms(30.0)  # 28000, the default ADB timeout less its margin


def stated(text: str) -> int | None:
    return from_instruction(text)[0]


# --- reading the instruction ------------------------------------------------


def test_the_stated_hold_time_is_read_in_seconds() -> None:
    assert stated("long press the MP3 for 3 seconds") == 3000


@pytest.mark.parametrize(
    "text, expected",
    [
        ("long press it for 800ms", 800),
        ("long press it for 2.5s", 2500),
        ("long press it for 3 secs", 3000),
        ("long press it for 1 second", 1000),
        ("long press it for 250 milliseconds", 250),
        ("press and hold the button for 4 seconds", 4000),
        ("tap and hold the row for 2 seconds", 2000),
        ("Long-Pressing the file for 3 Seconds", 3000),
        ("hold the shutter for 5 seconds", 5000),
    ],
)
def test_every_spelling_of_a_stated_hold_parses(text: str, expected: int) -> None:
    assert stated(text) == expected


def test_a_duration_belonging_to_wait_is_not_read_as_a_hold() -> None:
    """The false positive the nearest-verb rule exists to stop. A pattern that
    only asked for a hold verb somewhere earlier in the sentence reads both of
    these as a ten second hold."""
    assert stated("wait for 10 seconds then long press the MP3") is None
    assert stated("long press the icon, then wait for 10 seconds") is None


def test_a_duration_with_no_verb_before_it_is_ignored() -> None:
    assert stated("for 3 seconds") is None
    assert stated("open the chat and swipe for 2 seconds") is None


def test_an_instruction_with_no_duration_leaves_the_choice_open() -> None:
    value, notes = from_instruction("long press the MP3 and pick Forward")
    assert value is None
    assert notes == ()


def test_two_different_hold_times_disable_enforcement_rather_than_guess() -> None:
    value, notes = from_instruction(
        "long press A for 2 seconds then long press B for 5 seconds"
    )
    assert value is None
    assert notes and "2 different hold times" in notes[0]
    assert "2000ms, 5000ms" in notes[0]


def test_two_identical_hold_times_are_not_a_conflict() -> None:
    """There is nothing to guess between 3s and 3s, so enforcement stands."""
    assert stated("long press A for 3 seconds then long press B for 3 seconds") == 3000


def test_the_stated_time_is_recorded_in_words() -> None:
    assert from_instruction("long press it for 3 seconds")[1] == (
        "instruction asked for 3s",
    )


# --- reading what the model emitted -----------------------------------------


def test_a_plain_millisecond_value_passes_through_unremarked() -> None:
    assert from_arguments({"duration_ms": 3000}) == (3000, ())


def test_a_missing_duration_is_not_an_error() -> None:
    assert from_arguments({"action": "long_press", "coordinate": [1, 2]}) == (None, ())


def test_seconds_written_into_the_millisecond_key_are_repaired() -> None:
    """`duration_ms: 3` reaching the device unrepaired is a 3ms hold, which
    Android delivers as a tap while every artifact still reads 3."""
    value, notes = from_arguments({"duration_ms": 3})
    assert value == 3000
    assert len(notes) == 1
    assert "read as seconds and repaired to 3000ms" in notes[0]


def test_qwens_own_seconds_key_is_accepted() -> None:
    value, notes = from_arguments({"time": 3})
    assert value == 3000
    assert "Qwen's seconds key" in notes[0]


def test_duration_ms_wins_when_the_model_sends_both_keys() -> None:
    assert from_arguments({"duration_ms": 2000, "time": 9})[0] == 2000


def test_a_numeric_string_is_coerced_and_the_coercion_is_noted() -> None:
    value, notes = from_arguments({"duration_ms": "2000"})
    assert value == 2000
    assert notes == ("model sent duration_ms as the string '2000'",)


def test_the_repair_threshold_sits_where_no_press_can_be() -> None:
    assert from_arguments({"duration_ms": SECONDS_CEILING_MS})[0] == SECONDS_CEILING_MS
    assert from_arguments({"duration_ms": SECONDS_CEILING_MS - 1})[0] == 49000


@pytest.mark.parametrize("raw", ["1s", None, [], {}, True, "", -5, 0])
def test_a_value_that_is_not_a_duration_is_the_models_fault(raw: object) -> None:
    """ValueError so the runner files it as parse_error. Raised from inside
    device.execute instead, an unguarded int() made a model typo look like a
    dead device."""
    with pytest.raises(ValueError, match="duration_ms"):
        from_arguments({"duration_ms": raw})


# --- resolving ---------------------------------------------------------------


def test_the_instruction_overrides_the_model_and_says_so() -> None:
    held = resolve(3000, 800, 1000, FLOOR, CEILING)
    assert held.ms == 3000
    assert held.source == "instruction"
    assert held.requested_ms == 3000
    assert held.model_ms == 800
    assert held.notes == ("model asked for 800ms, overridden by the instruction",)


def test_a_model_that_agrees_with_the_instruction_is_not_reported_as_overridden() -> None:
    assert resolve(3000, 3000, 1000, FLOOR, CEILING).notes == ()


def test_the_model_decides_when_the_instruction_is_silent() -> None:
    held = resolve(None, 800, 1000, FLOOR, CEILING)
    assert (held.ms, held.source, held.model_ms) == (800, "model", 800)


def test_the_configured_default_is_the_last_resort() -> None:
    held = resolve(None, None, 1000, FLOOR, CEILING)
    assert (held.ms, held.source, held.requested_ms, held.model_ms) == (
        1000,
        "default",
        1000,
        None,
    )


def test_a_hold_under_the_device_threshold_is_raised_not_silently_tapped() -> None:
    held = resolve(None, 200, 1000, FLOOR, CEILING)
    assert held.ms == FLOOR
    assert held.requested_ms == 200
    assert "delivered as a tap" in held.notes[0]


def test_a_hold_over_the_adb_ceiling_is_lowered_and_recorded() -> None:
    held = resolve(60000, None, 1000, FLOOR, CEILING)
    assert held.ms == CEILING
    assert held.requested_ms == 60000
    assert f"lowered to {CEILING}ms" in held.notes[-1]


def test_the_ceiling_wins_over_a_floor_that_would_outlast_the_adb_call() -> None:
    """A device threshold longer than the ADB timeout would make every hold
    kill its own call. Returning late beats not returning."""
    held = resolve(None, None, 9000, 9000, ceiling_ms(5.0))
    assert held.ms == 3000


def test_upstream_notes_are_carried_into_the_record() -> None:
    held = resolve(3000, 3000, 1000, FLOOR, CEILING, notes=("instruction asked for 3s",))
    assert held.notes == ("instruction asked for 3s",)


def test_the_hold_record_is_json_ready() -> None:
    assert resolve(3000, 800, 1000, FLOOR, CEILING).as_dict() == {
        "ms": 3000,
        "source": "instruction",
        "requested_ms": 3000,
        "model_ms": 800,
        "notes": ["model asked for 800ms, overridden by the instruction"],
    }


# --- the ceiling itself ------------------------------------------------------


def test_the_ceiling_is_derived_from_the_adb_timeout_not_set_beside_it() -> None:
    """`input swipe` blocks for the whole hold, so the two are one number."""
    assert ceiling_ms(30.0) == 28000
    assert ceiling_ms(10.0) == 8000


def test_an_adb_timeout_shorter_than_the_margin_still_yields_a_usable_hold() -> None:
    assert ceiling_ms(1.0) == 1
    assert ceiling_ms(0.5) == 1


def test_the_fallback_floor_is_androids_own_default() -> None:
    assert DEFAULT_FLOOR_MS == 500
