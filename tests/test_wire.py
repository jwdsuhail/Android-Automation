"""The two rewrites that stand between a correct request and a working one."""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from android_runner import qwen_vl, wire


def png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(120, 30, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def data_url(raw: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def image_message(raw: bytes) -> dict:
    return {
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": data_url(raw)}},
            {"type": "text", "text": "The screen now."},
        ],
    }


def decode(message: dict) -> bytes:
    url = message["content"][0]["image_url"]["url"]
    return base64.b64decode(url.split(",", 1)[1])


def test_escape_rewrites_tags_without_moving_the_block() -> None:
    raw = '<tool_call>\n{"name": "mobile_use"}\n</tool_call>'
    assert (
        wire.escape_tool_call_tags(raw)
        == '[tool_call]\n{"name": "mobile_use"}\n[/tool_call]'
    )


def test_escape_leaves_ordinary_prose_untouched() -> None:
    assert wire.escape_tool_call_tags("Action: tap New Booking") == "Action: tap New Booking"


def test_escape_reaches_the_system_prompt_and_replayed_turns() -> None:
    """Both carry the tags, so escaping only the prompt still crashes at step 2."""
    messages = [
        {"role": "system", "content": qwen_vl.SYSTEM_PROMPT},
        {"role": "assistant", "content": 'Action: tap\n<tool_call>{"a": 1}</tool_call>'},
        image_message(png(10, 10)),
    ]
    out = wire.apply(messages, escape_tool_calls=True, max_image_pixels=0)
    for message in out:
        assert "<tool_call>" not in str(message["content"])
    assert "[tool_call]" in out[0]["content"]
    assert "[tool_call]" in out[1]["content"]


def test_escape_is_off_by_default_flag() -> None:
    messages = [{"role": "system", "content": "<tool_call>{}</tool_call>"}]
    out = wire.apply(messages, escape_tool_calls=False, max_image_pixels=0)
    assert out[0]["content"] == "<tool_call>{}</tool_call>"


def test_escaped_output_still_parses() -> None:
    """Escaping is a round trip: what we send bracketed, we can read back."""
    reply = 'Action: tap the icon\n<tool_call>{"name": "mobile_use", "arguments": {"action": "click", "coordinate": [10, 20]}}</tool_call>'
    parsed = qwen_vl.parse(wire.escape_tool_call_tags(reply))
    assert parsed.action == "click"
    assert parsed.arguments["coordinate"] == [10, 20]


def test_downscale_shrinks_a_real_screenshot_below_the_budget() -> None:
    original = png(1080, 2424)
    assert 1080 * 2424 > qwen_vl.MAX_PIXELS
    shrunk = wire.downscale_png(original, qwen_vl.MAX_PIXELS)
    with Image.open(io.BytesIO(shrunk)) as image:
        width, height = image.size
    assert width * height <= qwen_vl.MAX_PIXELS
    # Aspect ratio survives, or the 0-1000 grid stops mapping back onto the
    # full-resolution screenshot.
    assert abs((width / height) - (1080 / 2424)) < 0.01


def test_downscale_leaves_a_small_image_byte_identical() -> None:
    small = png(100, 100)
    assert wire.downscale_png(small, qwen_vl.MAX_PIXELS) is small


def test_downscale_disabled_returns_input() -> None:
    original = png(1080, 2424)
    assert wire.downscale_png(original, 0) is original


def test_effective_max_pixels_resolves_the_three_cases() -> None:
    assert wire.effective_max_pixels(0, qwen_vl.MAX_PIXELS) == qwen_vl.MAX_PIXELS
    assert wire.effective_max_pixels(500_000, qwen_vl.MAX_PIXELS) == 500_000
    assert wire.effective_max_pixels(-1, qwen_vl.MAX_PIXELS) == 0


def test_apply_downscales_every_image_part() -> None:
    messages = [image_message(png(1080, 2424)), image_message(png(1080, 2424))]
    out = wire.apply(messages, escape_tool_calls=False, max_image_pixels=qwen_vl.MAX_PIXELS)
    for message in out:
        with Image.open(io.BytesIO(decode(message))) as image:
            assert image.size[0] * image.size[1] <= qwen_vl.MAX_PIXELS
        assert message["content"][1]["text"] == "The screen now."


def test_apply_rejects_a_non_png_data_url() -> None:
    message = {
        "role": "user",
        "content": [{"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}],
    }
    with pytest.raises(ValueError, match="image url"):
        wire.apply([message], escape_tool_calls=False, max_image_pixels=1000)
