"""Configuration refusals that must stay loud."""

from __future__ import annotations

import pytest

from android_runner.config import Settings


def test_reflection_requires_two_history_images(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADB_DEVICE", "emulator-5554")
    monkeypatch.setenv("MODEL_HISTORY_N", "1")
    monkeypatch.setenv("MODEL_REFLECTION", "1")
    with pytest.raises(ValueError, match="at least 2"):
        Settings.from_env()


def test_reflection_can_be_disabled_with_history_n_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADB_DEVICE", "emulator-5554")
    monkeypatch.setenv("MODEL_HISTORY_N", "1")
    monkeypatch.setenv("MODEL_REFLECTION", "0")
    settings = Settings.from_env()
    assert settings.model_reflection is False
    assert settings.model_history_n == 1


def test_reflection_defaults_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADB_DEVICE", "emulator-5554")
    monkeypatch.setenv("MODEL_HISTORY_N", "3")
    monkeypatch.delenv("MODEL_REFLECTION", raising=False)
    monkeypatch.delenv("MODEL_THINKING", raising=False)
    settings = Settings.from_env()
    assert settings.model_reflection is True
    assert settings.model_thinking is False


def test_a_hold_longer_than_the_adb_call_is_refused_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`input swipe` blocks for the whole hold, so a hold past ADB_TIMEOUT_S
    kills its own call. The bound is derived from that timeout rather than
    being a second number that can drift away from it."""
    monkeypatch.setenv("ADB_DEVICE", "emulator-5554")
    monkeypatch.setenv("ADB_TIMEOUT_S", "10")
    monkeypatch.setenv("ADB_LONG_PRESS_MS", "9000")
    with pytest.raises(ValueError, match="between 1 and 8000"):
        Settings.from_env()


def test_a_longer_adb_timeout_permits_a_longer_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADB_DEVICE", "emulator-5554")
    monkeypatch.setenv("ADB_TIMEOUT_S", "60")
    monkeypatch.setenv("ADB_LONG_PRESS_MS", "9000")
    assert Settings.from_env().long_press_ms == 9000


def test_no_app_is_force_stopped_unless_one_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hardcoded package made every run start on the launcher, so the agent
    spent turn 0 opening the app and was shown the next screen mid-draw. The
    isolation is still available; it is no longer the default."""
    monkeypatch.setenv("ADB_DEVICE", "emulator-5554")
    monkeypatch.delenv("APP_PACKAGE", raising=False)
    assert Settings.from_env().app_package == ""


def test_an_app_package_can_still_be_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADB_DEVICE", "emulator-5554")
    monkeypatch.setenv("APP_PACKAGE", " com.example.other ")
    assert Settings.from_env().app_package == "com.example.other"


def test_the_settle_cap_defaults_to_three_seconds_and_zero_turns_it_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0 has to mean off rather than a startup refusal: it is the escape hatch
    for a device where polling costs more than the half-drawn screen does."""
    monkeypatch.setenv("ADB_DEVICE", "emulator-5554")
    monkeypatch.delenv("MODEL_SETTLE_TIMEOUT_S", raising=False)
    assert Settings.from_env().settle_timeout_s == 3
    monkeypatch.setenv("MODEL_SETTLE_TIMEOUT_S", "0")
    assert Settings.from_env().settle_timeout_s == 0
    monkeypatch.setenv("MODEL_SETTLE_TIMEOUT_S", "-4")
    assert Settings.from_env().settle_timeout_s == 0
