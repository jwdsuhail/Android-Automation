"""Configuration loaded from environment variables and an optional local .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from android_runner import hold


def load_env_file(path: Path | None = None) -> list[str]:
    """Load unset variables from a simple KEY=VALUE file."""
    env_path = path or Path.cwd() / ".env"
    if not env_path.is_file():
        return []

    loaded: list[str] = []
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ[key] = value
        loaded.append(key)
    return loaded


def _positive_float(name: str, default: str) -> float:
    value = float(os.environ.get(name, default))
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _bool(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    model_base_url: str
    model_name: str
    model_api_key: str
    model_timeout_s: float
    model_escape_tool_calls: bool
    model_max_image_pixels: int
    model_thinking: bool
    model_reasoning_effort: str
    model_reflection: bool
    model_history_n: int
    model_temperature: float
    model_max_tokens: int
    adb_path: str
    adb_device: str
    adb_timeout_s: float
    step_sleep_s: float
    long_press_ms: int
    settle_timeout_s: float
    app_package: str

    @classmethod
    def from_env(cls) -> "Settings":
        device = os.environ.get("ADB_DEVICE", "").strip()
        if not device:
            raise ValueError("ADB_DEVICE is required; refusing to guess a device")

        history_n = int(os.environ.get("MODEL_HISTORY_N", "3"))
        if history_n < 1:
            raise ValueError("MODEL_HISTORY_N must be at least 1")

        model_reflection = _bool("MODEL_REFLECTION", "1")
        if model_reflection and history_n < 2:
            raise ValueError(
                "MODEL_HISTORY_N must be at least 2 when MODEL_REFLECTION is enabled"
            )

        # Negative disables downscaling, 0 means the model's own budget, so
        # this one is deliberately not range-checked below zero.
        max_image_pixels = int(os.environ.get("MODEL_MAX_IMAGE_PIXELS", "0"))

        reasoning_effort = os.environ.get("MODEL_REASONING_EFFORT", "auto").strip().lower()
        if reasoning_effort not in {"auto", "none", "low", "medium", "high"}:
            raise ValueError(
                "MODEL_REASONING_EFFORT must be auto, none, low, medium, or high"
            )

        # `input swipe` blocks the ADB call for the whole hold, so the longest
        # usable hold is a consequence of ADB_TIMEOUT_S rather than a third
        # number to keep in sync with it. No lower bound beyond zero: a hold
        # under the device's own long-press threshold is raised by hold.resolve,
        # which says so in the turn record instead of doing it silently.
        adb_timeout_s = _positive_float("ADB_TIMEOUT_S", "30")
        ceiling_ms = hold.ceiling_ms(adb_timeout_s)
        long_press_ms = int(os.environ.get("ADB_LONG_PRESS_MS", "1000"))
        if not 0 < long_press_ms <= ceiling_ms:
            raise ValueError(
                f"ADB_LONG_PRESS_MS must be between 1 and {ceiling_ms}, the most "
                f"that fits inside ADB_TIMEOUT_S of {adb_timeout_s}s"
            )

        return cls(
            model_base_url=os.environ.get(
                "MODEL_BASE_URL", "http://127.0.0.1:8000/v1"
            ),
            model_name=os.environ.get("MODEL_MODEL", "qwen3_vl"),
            model_api_key=os.environ.get("MODEL_API_KEY", "empty"),
            model_timeout_s=_positive_float("MODEL_TIMEOUT_S", "180"),
            model_escape_tool_calls=_bool("MODEL_ESCAPE_TOOL_CALLS"),
            model_max_image_pixels=max_image_pixels,
            model_thinking=_bool("MODEL_THINKING"),
            model_reasoning_effort=reasoning_effort,
            model_reflection=model_reflection,
            model_history_n=history_n,
            model_temperature=float(os.environ.get("MODEL_TEMPERATURE", "0")),
            model_max_tokens=int(os.environ.get("MODEL_MAX_TOKENS", "2048")),
            adb_path=os.environ.get("ADB_PATH", "adb"),
            adb_device=device,
            adb_timeout_s=adb_timeout_s,
            step_sleep_s=max(0.0, float(os.environ.get("MODEL_STEP_SLEEP", "1"))),
            long_press_ms=long_press_ms,
            settle_timeout_s=max(
                0.0, float(os.environ.get("MODEL_SETTLE_TIMEOUT_S", "3"))
            ),
            # No default package. Force-stopping one named app made every run
            # start on the launcher, which is a cold start the agent then has
            # to spend a turn on and a screenshot the app has not finished
            # drawing. Set APP_PACKAGE to opt back into that isolation.
            app_package=os.environ.get("APP_PACKAGE", "").strip(),
        )
