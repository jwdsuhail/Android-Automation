"""Payload rewrites the target server needs. Model-agnostic.

Two adjustments stand between a correct request and a working one:
Ollama rejects the tool-call tags this prompt is built from, and a screenshot
sent above the model's training resolution grounds worse. Both operate on
already-built messages, so prompt construction stays in one place.

Does not build prompts, talk to ADB, or call HTTP.
"""

from __future__ import annotations

import base64
import io
import math
from typing import Any

from PIL import Image

_DATA_URL_PREFIX = "data:image/png;base64,"

# Ollama's tool-call parser scans *prompt* text for these tags even when the
# request declares no tools, and answers 500 {"error":"EOF"} when it finds them
# (ollama/ollama#14986, fix PR #15011 still open). The system prompt carries
# them and so does every replayed assistant turn, so a run crashes at step 1
# and would crash at step 2 even with a clean prompt.
_OPEN_TAG = "<tool_call>"
_CLOSE_TAG = "</tool_call>"
_SAFE_OPEN = "[tool_call]"
_SAFE_CLOSE = "[/tool_call]"


def escape_tool_call_tags(text: str) -> str:
    """Rewrite <tool_call>...</tool_call> as [tool_call]...[/tool_call].

    Purely textual and line-for-line: the block keeps its position and its JSON
    body, so the prompt still asks for the same call and a replayed assistant
    turn still reads as one. Brackets rather than a ```tool_call fence, because
    the prompt already uses fences for its own examples and the two would nest.
    `qwen_vl.parse` reads either spelling back.
    """
    return text.replace(_OPEN_TAG, _SAFE_OPEN).replace(_CLOSE_TAG, _SAFE_CLOSE)


def _part_url(part: dict[str, Any]) -> str:
    value = part.get("image_url")
    if isinstance(value, dict):
        return str(value.get("url", ""))
    return str(value or "")


def _bare_base64(url: str) -> str:
    if not url.startswith(_DATA_URL_PREFIX):
        raise ValueError(f"expected a {_DATA_URL_PREFIX!r} image url, got {url[:40]!r}")
    return url[len(_DATA_URL_PREFIX) :]


def downscale_png(png: bytes, max_pixels: int) -> bytes:
    """Shrink to at most max_pixels, preserving aspect ratio.

    The budget is the resolution the model was trained at; above it grounding
    accuracy drops. Aspect ratio is preserved so a normalised coordinate still
    maps onto the full-resolution screenshot: the model answers on a 0-1000
    grid, which does not move when the image does, and the tap is scaled
    against the original screenshot size rather than this shrunk copy.
    """
    if max_pixels <= 0:
        return png
    with Image.open(io.BytesIO(png)) as image:
        width, height = image.size
        if width * height <= max_pixels:
            return png
        scale = math.sqrt(max_pixels / (width * height))
        size = (max(1, int(width * scale)), max(1, int(height * scale)))
        resized = image.convert("RGB").resize(size, Image.LANCZOS)
    buffer = io.BytesIO()
    resized.save(buffer, format="PNG")
    return buffer.getvalue()


def effective_max_pixels(override: int, default_max_pixels: int) -> int:
    """Resolve the downscale budget.

    A positive MODEL_MAX_IMAGE_PIXELS wins, a negative one disables downscaling,
    and 0 means use the model's own budget. Honouring the training resolution
    is the accurate default rather than an optimisation, so 0 is not "off".
    """
    if override > 0:
        return override
    if override < 0:
        return 0
    return default_max_pixels


def apply(
    messages: list[dict[str, Any]],
    *,
    escape_tool_calls: bool,
    max_image_pixels: int,
) -> list[dict[str, Any]]:
    """Rewrite every message for the target server.

    Escaping runs over each text part wherever it sits — system string content
    and assistant replays alike — because the tags reach the server from both.
    """
    out: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")

        if isinstance(content, str):
            if escape_tool_calls:
                message = {**message, "content": escape_tool_call_tags(content)}
            out.append(message)
            continue

        if not isinstance(content, list):
            out.append(message)
            continue

        parts: list[Any] = []
        for part in content:
            if not isinstance(part, dict):
                parts.append(part)
            elif part.get("type") == "text" and escape_tool_calls:
                parts.append(
                    {**part, "text": escape_tool_call_tags(str(part.get("text", "")))}
                )
            elif part.get("type") == "image_url" and max_image_pixels > 0:
                png = base64.b64decode(_bare_base64(_part_url(part)))
                shrunk = base64.b64encode(
                    downscale_png(png, max_image_pixels)
                ).decode("ascii")
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": _DATA_URL_PREFIX + shrunk},
                    }
                )
            else:
                parts.append(part)
        out.append({**message, "content": parts})
    return out
