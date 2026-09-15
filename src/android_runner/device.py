"""ADB device operations. This module has no model dependencies."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from PIL import Image

from android_runner import hold
from android_runner.config import Settings

SYSTEM_KEYS = {"back": 4, "home": 3, "menu": 82, "enter": 66}


def _type_payload(text: str) -> str:
    if any(ord(character) < 0x20 or ord(character) > 0x7E for character in text):
        raise ValueError("ADB input text supports printable ASCII only")
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace(" ", "%s")
    for character in ("&", "<", ">", "|", ";", "*", "(", ")", '"', "'"):
        escaped = escaped.replace(character, "\\" + character)
    return escaped


class AdbDevice:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _command(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [
                self.settings.adb_path,
                "-s",
                self.settings.adb_device,
                *args,
            ],
            check=True,
            capture_output=True,
            timeout=self.settings.adb_timeout_s,
        )

    def screenshot(self, path: Path) -> tuple[bytes, int, int]:
        png = self._command("exec-out", "screencap", "-p").stdout
        if not png.startswith(b"\x89PNG"):
            raise RuntimeError("adb screencap did not return PNG data")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        with Image.open(path) as image:
            width, height = image.size
        return png, width, height

    def long_press_floor_ms(self) -> int:
        """The shortest hold this device counts as a long press.

        ViewConfiguration reads this setting, so a hold below it is delivered
        as a plain tap however long the caller asked for - which is exactly
        how a unit mistake hides. One cheap shell call, not uiautomator.

        Some builds leave the setting unset or answer "null". That is not
        worth ending a run over, so Android's own default stands in; a dead
        device will surface a sentence later on the entry screenshot anyway.
        """
        try:
            raw = self._command(
                "shell", "settings", "get", "secure", "long_press_timeout"
            )
        except (subprocess.SubprocessError, OSError):
            return hold.DEFAULT_FLOOR_MS
        value = raw.stdout.decode("utf-8", errors="replace").strip()
        if not value.isdigit() or int(value) <= 0:
            return hold.DEFAULT_FLOOR_MS
        return int(value)

    def execute(self, action: dict[str, Any]) -> None:
        name = action.get("action")
        if name == "click":
            x, y = action["coordinate"]
            self._command("shell", "input", "tap", str(x), str(y))
        elif name == "long_press":
            x, y = action["coordinate"]
            # Not clamped here. How long a hold lasts is policy, decided by
            # hold.resolve before this is called and written into the turn
            # record; a second clamp at this depth is what silently turned a
            # three second hold into a 200ms tap and told nobody.
            duration = int(action.get("duration_ms", self.settings.long_press_ms))
            self._command(
                "shell",
                "input",
                "swipe",
                str(x),
                str(y),
                str(x),
                str(y),
                str(duration),
            )
        elif name == "type":
            text = str(action.get("text", ""))
            if text:
                self._command("shell", "input", "text", _type_payload(text))
        elif name in {"swipe", "drag"}:
            start_key = "coordinate" if name == "swipe" else "start_coordinate"
            end_key = "coordinate2" if name == "swipe" else "end_coordinate"
            x1, y1 = action[start_key]
            x2, y2 = action[end_key]
            self._command(
                "shell",
                "input",
                "swipe",
                str(x1),
                str(y1),
                str(x2),
                str(y2),
                "500",
            )
        elif name == "system_button":
            button = str(action.get("button", "")).lower()
            if button not in SYSTEM_KEYS:
                raise ValueError(f"unknown system button {button!r}")
            self._command("shell", "input", "keyevent", str(SYSTEM_KEYS[button]))
        elif name == "wait":
            seconds = max(0.0, min(15.0, float(action.get("time", 1))))
            time.sleep(seconds)
        elif name == "terminate":
            return
        else:
            raise ValueError(f"cannot execute action {name!r}")
