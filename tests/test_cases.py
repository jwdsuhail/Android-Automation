"""The case store: one JSON file per case, and one place that validates them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from android_runner.cases import Case, CaseStore, slugify


def a_case(**overrides: object) -> Case:
    fields: dict[str, object] = {
        "id": "audio-metadata",
        "name": "Audio metadata",
        "instruction": "Open the Test 14 chat and update the audio metadata",
        "success": "the audio message is labelled Verified Track",
    }
    fields.update(overrides)
    return Case(**fields)  # type: ignore[arg-type]


def test_slugify_makes_a_filename_from_a_human_name() -> None:
    assert slugify("Audio metadata") == "audio-metadata"
    assert slugify("  Test 14: the MP3!  ") == "test-14-the-mp3"
    assert slugify("Edit -- the file") == "edit-the-file"


def test_slugify_returns_empty_rather_than_inventing_a_name() -> None:
    """A name with nothing usable in it is the caller's problem to report.

    `case-1` would be a worse answer than asking for a different name.
    """
    assert slugify("!!!") == ""
    assert slugify("   ") == ""


def test_a_case_round_trips_through_disk(tmp_path: Path) -> None:
    store = CaseStore(tmp_path)
    saved = store.save(a_case())
    loaded = store.get("audio-metadata")

    assert loaded is not None
    assert loaded.instruction == saved.instruction
    assert loaded.success == saved.success
    assert loaded.max_actions == 20
    assert json.loads((tmp_path / "audio-metadata.json").read_text())["name"] == "Audio metadata"


def test_saving_stamps_the_times(tmp_path: Path) -> None:
    store = CaseStore(tmp_path)
    saved = store.save(a_case())
    assert saved.created_at.endswith("Z")
    assert saved.updated_at.endswith("Z")

    again = store.save(saved)
    assert again.created_at == saved.created_at


def test_unique_id_steps_around_a_name_already_taken(tmp_path: Path) -> None:
    store = CaseStore(tmp_path)
    store.save(a_case())
    assert store.unique_id("audio-metadata") == "audio-metadata-2"
    store.save(a_case(id="audio-metadata-2"))
    assert store.unique_id("audio-metadata") == "audio-metadata-3"
    assert store.unique_id("something-else") == "something-else"


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"max_actions": 0}, "max_actions must be at least 1"),
        ({"max_waits": 0}, "max_waits must be at least 1"),
        ({"verify_timeout_s": -1}, "verify_timeout_s must be greater than 0"),
        ({"instruction": "   "}, "instruction must not be empty"),
        ({"name": ""}, "name must not be empty"),
        ({"id": "Not A Slug"}, "must be lowercase letters"),
    ],
)
def test_validation_refuses_what_the_runner_could_not_honour(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        a_case(**overrides).validate()


def test_an_empty_success_is_not_the_same_as_no_success() -> None:
    """Omitting the condition is an exploration run. Blanking it is a typo."""
    a_case(success=None).validate()
    with pytest.raises(ValueError, match="either describe a visible condition"):
        a_case(success="  ").validate()


def test_numbers_arriving_as_strings_from_a_form_are_coerced() -> None:
    case = Case.from_dict(
        {
            "name": "Audio metadata",
            "instruction": "Open the chat",
            "max_actions": "70",
        }
    )
    assert case.max_actions == 70
    assert case.id == "audio-metadata"


def test_a_case_file_from_before_the_wall_clock_was_dropped_still_loads() -> None:
    """There is no migration step and there should not be one: a key nothing
    reads any more is a key from a later - or earlier - version, and from_dict
    already promises to ignore those rather than refuse the file."""
    case = Case.from_dict(
        {
            "name": "Audio metadata",
            "instruction": "Open the chat",
            "wall_clock_s": 2400,
        }
    )
    case.validate()
    assert not hasattr(case, "wall_clock_s")
    assert "wall_clock_s" not in case.as_dict()


def test_a_number_that_is_not_a_number_says_which_field() -> None:
    with pytest.raises(ValueError, match="max_actions must be a whole number"):
        Case.from_dict({"name": "x", "instruction": "y", "max_actions": "seventy"})


def test_unknown_keys_are_ignored_so_a_newer_file_still_loads() -> None:
    case = Case.from_dict(
        {"name": "x", "instruction": "y", "invented_later": {"deeply": "nested"}}
    )
    assert case.name == "x"


def test_a_malformed_case_is_named_rather_than_hidden(tmp_path: Path) -> None:
    """The same rule `reader.py` applies to a run that never finished.

    A case you cannot see is a case you cannot fix.
    """
    store = CaseStore(tmp_path)
    store.save(a_case())
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")

    listing = store.list()
    assert [c.id for c in listing.cases] == ["audio-metadata"]
    assert [b.id for b in listing.broken] == ["broken"]
    assert listing.broken[0].error


def test_the_store_refuses_an_id_that_is_a_path(tmp_path: Path) -> None:
    """`id` arrives from a URL, so it is checked rather than joined blind."""
    store = CaseStore(tmp_path)
    secret = tmp_path.parent / "secret.json"
    secret.write_text('{"name": "s", "instruction": "s"}', encoding="utf-8")

    assert store.get("../secret") is None
    assert store.exists("../secret") is False
    assert store.delete("../secret") is False
    assert secret.is_file()


def test_delete_reports_whether_anything_was_there(tmp_path: Path) -> None:
    store = CaseStore(tmp_path)
    store.save(a_case())
    assert store.delete("audio-metadata") is True
    assert store.delete("audio-metadata") is False


def test_listing_puts_the_most_recently_edited_first(tmp_path: Path) -> None:
    store = CaseStore(tmp_path)
    store.save(a_case(id="first", name="First", created_at="2026-01-01T00:00:00Z"))
    store.save(a_case(id="second", name="Second"))
    # `updated_at` is stamped at save time, so the second save sorts first.
    ids = [c.id for c in store.list().cases]
    assert ids[0] == "second"
