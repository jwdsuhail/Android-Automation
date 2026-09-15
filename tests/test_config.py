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


def test_the_app_under_test_defaults_to_the_chat_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADB_DEVICE", "emulator-5554")
    monkeypatch.delenv("APP_PACKAGE", raising=False)
    assert Settings.from_env().app_package == "com.xuper.chat.app"


def test_an_empty_app_package_turns_the_close_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not a missing value. Running against another app is a real case, and
    it must not be reachable only by editing the source."""
    monkeypatch.setenv("ADB_DEVICE", "emulator-5554")
    monkeypatch.setenv("APP_PACKAGE", "  ")
    assert Settings.from_env().app_package == ""
