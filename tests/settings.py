"""Settings shared by the test modules. Lives here so importing it does
not depend on the working directory pytest was invoked from."""

from __future__ import annotations

from android_runner.config import Settings

SETTINGS = Settings(
    model_base_url="http://example.test/v1",
    model_name="test",
    model_api_key="test",
    model_timeout_s=10,
    model_escape_tool_calls=False,
    model_max_image_pixels=0,
    model_thinking=False,
    model_reasoning_effort="auto",
    model_reflection=True,
    model_history_n=3,
    model_temperature=0,
    model_max_tokens=100,
    adb_path="adb",
    adb_device="emulator-5554",
    adb_timeout_s=10,
    step_sleep_s=0,
    long_press_ms=1000,
    # Off, so a test that does not care about settling takes one screenshot per
    # capture as it always did. The tests that do care raise it themselves and
    # inject a clock, because the real deadline would otherwise be measured
    # against wall time these fakes never spend.
    settle_timeout_s=0,
    # Not the default any more - APP_PACKAGE is empty unless someone opts in -
    # but set here so the runner tests still exercise the close rather than a
    # disabled version of it.
    app_package="com.xuper.chat.app",
)
