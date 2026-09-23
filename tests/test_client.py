"""Transport behaviour: the reasoning channel, wire rewrites, and diagnostics."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from android_runner import client as client_module
from android_runner.client import Completion, ModelClient, truncation_note
from settings import SETTINGS


class FakeMessage:
    def __init__(self, content: str, **fields: Any) -> None:
        self.content = content
        for name, value in fields.items():
            setattr(self, name, value)


class FakeCompletions:
    def __init__(self, message: FakeMessage, finish_reason: str = "stop") -> None:
        self.message = message
        self.finish_reason = finish_reason
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        choice = type("Choice", (), {"message": self.message, "finish_reason": self.finish_reason})
        usage = type("Usage", (), {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        return type("Response", (), {"choices": [choice()], "usage": usage()})()


class FakeOpenAI:
    last: "FakeOpenAI"

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.completions = FakeCompletions(FakeMessage("Action: ok"))
        self.chat = type("Chat", (), {"completions": self.completions})()
        FakeOpenAI.last = self

    def with_options(self, **_kwargs: Any) -> "FakeOpenAI":
        return self


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(client_module, "OpenAI", FakeOpenAI)
    return FakeOpenAI


def build(settings: Any = SETTINGS) -> ModelClient:
    return ModelClient(settings)


def test_sdk_retries_are_off_so_the_timeout_asked_for_is_the_one_paid(
    fake_openai: Any,
) -> None:
    """SDK retries reapply the full timeout per attempt and can multiply it."""
    build()
    assert FakeOpenAI.last.init_kwargs["max_retries"] == 0


def test_reasoning_content_is_read(fake_openai: Any) -> None:
    model = build()
    FakeOpenAI.last.completions.message = FakeMessage("Action: ok", reasoning_content="why")
    result = model.complete([{"role": "user", "content": "hi"}])
    assert result.reasoning == "why"
    assert result.reasoning_chars == 3


def test_the_reasoning_field_name_ollama_uses_is_also_read(fake_openai: Any) -> None:
    """Ollama sends `reasoning`; reading only `reasoning_content` loses it."""
    model = build()
    FakeOpenAI.last.completions.message = FakeMessage("Action: ok", reasoning="why")
    assert model.complete([{"role": "user", "content": "hi"}]).reasoning == "why"


def test_empty_content_with_the_reply_in_the_reasoning_channel(fake_openai: Any) -> None:
    """Observed live against Ollama: content "" and the whole reply in
    `reasoning`. The step is parseable, so it must not be failed."""
    model = build()
    FakeOpenAI.last.completions.message = FakeMessage(
        "", reasoning='Action: tap\n[tool_call]{"name": "mobile_use"}[/tool_call]'
    )
    result = model.complete([{"role": "user", "content": "hi"}])
    assert result.error is None
    assert result.raw.startswith("Action: tap")
    assert result.reasoning is None


def test_empty_content_and_no_reasoning_is_an_error(fake_openai: Any) -> None:
    model = build()
    FakeOpenAI.last.completions.message = FakeMessage("")
    assert model.complete([{"role": "user", "content": "hi"}]).error == "model returned empty content"


def test_transport_failure_is_reported_not_raised(fake_openai: Any) -> None:
    model = build()

    def boom(**_kwargs: Any) -> Any:
        raise RuntimeError("connection reset")

    FakeOpenAI.last.completions.create = boom
    result = model.complete([{"role": "user", "content": "hi"}])
    assert result.error == "RuntimeError: connection reset"
    assert result.raw == ""


def test_finish_reason_is_recorded(fake_openai: Any) -> None:
    model = build()
    FakeOpenAI.last.completions.finish_reason = "length"
    result = model.complete([{"role": "user", "content": "hi"}])
    assert result.finish_reason == "length"
    assert "MODEL_MAX_TOKENS" in truncation_note(result)


def test_truncation_note_is_empty_for_a_normal_stop() -> None:
    assert truncation_note(Completion(raw="x", latency_ms=1, finish_reason="stop")) == ""


def test_reasoning_effort_none_when_thinking_is_off(fake_openai: Any) -> None:
    """`auto` defers to MODEL_THINKING so the prompt and the server agree."""
    build().complete([{"role": "user", "content": "hi"}])
    assert FakeOpenAI.last.completions.calls[0]["extra_body"] == {"reasoning_effort": "none"}


def test_no_reasoning_field_is_sent_when_thinking_is_on(fake_openai: Any) -> None:
    build(replace(SETTINGS, model_thinking=True)).complete([{"role": "user", "content": "hi"}])
    assert FakeOpenAI.last.completions.calls[0]["extra_body"] is None


def test_an_explicit_effort_overrides_auto(fake_openai: Any) -> None:
    build(replace(SETTINGS, model_reasoning_effort="high")).complete(
        [{"role": "user", "content": "hi"}]
    )
    assert FakeOpenAI.last.completions.calls[0]["extra_body"] == {"reasoning_effort": "high"}


def test_tool_call_tags_are_escaped_on_the_way_out(fake_openai: Any) -> None:
    build(replace(SETTINGS, model_escape_tool_calls=True)).complete(
        [{"role": "system", "content": "<tool_call>{}</tool_call>"}]
    )
    sent = FakeOpenAI.last.completions.calls[0]["messages"]
    assert sent[0]["content"] == "[tool_call]{}[/tool_call]"


def test_tags_are_left_alone_when_the_flag_is_off(fake_openai: Any) -> None:
    build().complete([{"role": "system", "content": "<tool_call>{}</tool_call>"}])
    sent = FakeOpenAI.last.completions.calls[0]["messages"]
    assert sent[0]["content"] == "<tool_call>{}</tool_call>"
