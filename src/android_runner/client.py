"""The one place a chat completion is performed. Model-agnostic transport.

Owns the wire rewrites, the reasoning channel, and the metadata that explains
a failed turn. Does not build prompts or parse actions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from android_runner import qwen_vl, wire
from android_runner.config import Settings


@dataclass(frozen=True)
class Completion:
    """One completion.

    Transport and empty-content failures arrive as `error` rather than as a
    raise, because the runner records them as artifacts.
    """

    raw: str
    latency_ms: float
    usage: dict[str, int | None] | None = None
    error: str | None = None
    reasoning: str | None = None
    # How much reasoning came back. Zero when thinking is off and the server
    # obeyed; non-zero when it did not. A toggle you cannot verify is a wish.
    reasoning_chars: int = 0
    # "length" means the reply was cut off at max_tokens. Without this a
    # truncated reply and a model that refused to act produce the identical
    # "no tool_call block" error, which sends you looking in the wrong place.
    finish_reason: str | None = None


def truncation_note(completion: Completion) -> str:
    """A suffix explaining a parse failure caused by max_tokens, else ""."""
    if completion.finish_reason == "length":
        return " (the reply was truncated at max_tokens; raise MODEL_MAX_TOKENS)"
    return ""


def _usage(response: Any) -> dict[str, int | None] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def _reasoning_text(message: Any) -> str | None:
    """The thinking channel, whichever of its two names the server uses.

    vLLM and DeepSeek-style servers send `reasoning_content`; Ollama sends
    `reasoning`. Reading only the first name loses the entire reply on a
    server that picks the second.
    """
    for field in ("reasoning_content", "reasoning"):
        value = getattr(message, field, None)
        if isinstance(value, str) and value.strip():
            return value
    return None


class ModelClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = OpenAI(
            base_url=settings.model_base_url,
            api_key=settings.model_api_key,
            timeout=settings.model_timeout_s,
            # No SDK retries: they reapply the full timeout to every
            # attempt, so a call the runner asked to cap at 180s can quietly
            # take three times that. Each call is one attempt inside the
            # timeout the runner passes in.
            max_retries=0,
        )

    def _reasoning_effort(self) -> str | None:
        """The value to send, or None to send no reasoning field at all.

        `auto` defers to MODEL_THINKING, so switching the prompt off switches
        the server off too and the two layers cannot disagree.
        """
        configured = self.settings.model_reasoning_effort
        if configured != "auto":
            return configured
        return None if self.settings.model_thinking else "none"

    def complete(
        self, messages: list[dict[str, Any]], *, timeout_s: float | None = None
    ) -> Completion:
        payload = wire.apply(
            messages,
            escape_tool_calls=self.settings.model_escape_tool_calls,
            max_image_pixels=wire.effective_max_pixels(
                self.settings.model_max_image_pixels, qwen_vl.MAX_PIXELS
            ),
        )
        # `extra_body` rather than the typed kwarg: pyproject floors
        # openai>=1.40.0, where `reasoning_effort` does not exist as a named
        # parameter. extra_body merges into the JSON body on every version.
        extra: dict[str, Any] = {}
        effort = self._reasoning_effort()
        if effort is not None:
            extra["reasoning_effort"] = effort

        started = time.monotonic()
        try:
            response = self._client.with_options(
                timeout=timeout_s or self.settings.model_timeout_s
            ).chat.completions.create(
                model=self.settings.model_name,
                messages=payload,
                temperature=self.settings.model_temperature,
                max_tokens=self.settings.model_max_tokens,
                extra_body=extra or None,
            )
        except Exception as exc:  # noqa: BLE001 - recorded by the runner
            return Completion(
                raw="",
                latency_ms=(time.monotonic() - started) * 1000,
                error=f"{type(exc).__name__}: {exc}",
            )

        choice = response.choices[0]
        message = choice.message
        raw = message.content or ""
        reasoning = _reasoning_text(message)
        if not raw.strip() and reasoning:
            # A thinking model can put the whole reply - the tool call included
            # - in the reasoning channel and leave content empty. The reply is
            # complete and parseable; only its envelope is odd. Reported as
            # raw, not reasoning, because the parser splits thought from action
            # itself and would otherwise treat the call as part of the thinking.
            raw, reasoning = reasoning, None
        return Completion(
            raw=raw,
            latency_ms=(time.monotonic() - started) * 1000,
            usage=_usage(response),
            error=None if raw.strip() else "model returned empty content",
            reasoning=reasoning,
            reasoning_chars=len(reasoning or ""),
            finish_reason=getattr(choice, "finish_reason", None),
        )
