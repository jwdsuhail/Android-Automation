"""The oracle retries a dead transport and never retries a verdict."""

from __future__ import annotations

import io
import json
from typing import Any

from PIL import Image

from android_runner import qwen_vl
from android_runner.client import Completion
from android_runner.verification import INCONCLUSIVE, INFRASTRUCTURE, OK, verify
from settings import SETTINGS

TIMEOUT = "APITimeoutError: Request timed out."


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("L", (20, 20), color=0).save(buffer, format="PNG")
    return buffer.getvalue()


def verdict(status: str) -> Completion:
    body = json.dumps(
        {"name": "mobile_use", "arguments": {"action": "terminate", "status": status}}
    )
    return Completion(f"Action: verdict\n<tool_call>{body}</tool_call>", 1)


def failed(error: str = TIMEOUT) -> Completion:
    return Completion("", 1, error=error)


class ScriptedClient:
    """Hands back canned completions in order and remembers what was asked."""

    def __init__(self, replies: list[Completion]) -> None:
        self.replies = list(replies)
        self.asked: list[str] = []

    def complete(
        self, messages: list[dict[str, Any]], *, timeout_s: float | None = None
    ) -> Completion:
        self.asked.append(str(messages[1]["content"][0]["text"]))
        return self.replies.pop(0)


def check(client: ScriptedClient, attempts: int = 2):
    return verify(
        client,  # type: ignore[arg-type]
        png(),
        "the file is visible",
        SETTINGS.model_history_n,
        5.0,
        attempts,
        screenshot="turn_000.after.png",
        phase="final",
    )


def test_a_dead_transport_is_retried_and_the_next_answer_stands() -> None:
    client = ScriptedClient([failed(), verdict("success"), verdict("fail")])
    outcome = check(client)
    assert outcome.holds is True
    assert outcome.kind == OK
    assert outcome.attempts == 3  # two for the predicate, one for the complement
    assert outcome.errors == (TIMEOUT,)


def test_exhausted_retries_are_infrastructure_and_stop_there() -> None:
    client = ScriptedClient([failed(), failed()])
    outcome = check(client)
    assert outcome.holds is None
    assert outcome.kind == INFRASTRUCTURE
    assert outcome.errors == (TIMEOUT, TIMEOUT)
    # The complement is never put to a transport that just failed twice.
    assert len(client.asked) == 2


def test_both_fail_is_inconclusive_and_still_records_the_two_fails() -> None:
    """The last live run: Test 14 was not in Contacts, and the second question
    also came back fail, so there is no verdict but both answers are kept."""
    client = ScriptedClient([verdict("fail"), verdict("fail")])
    outcome = check(client)
    assert outcome.holds is None
    assert outcome.kind == INCONCLUSIVE
    assert outcome.condition == "fail"
    assert outcome.negation == "fail"
    """Asking again until the two answers differ is best-of-N, not checking."""
    client = ScriptedClient([verdict("success"), verdict("success")])
    outcome = check(client)
    assert outcome.holds is None
    assert outcome.kind == INCONCLUSIVE
    assert "same answer to the predicate and its complement" in outcome.detail
    assert outcome.condition == "success"
    assert outcome.negation == "success"
    assert len(client.asked) == 2
    assert outcome.attempts == 2


def test_a_malformed_reply_is_not_retried() -> None:
    """Temperature is 0, so the second call returns the first call's answer."""
    client = ScriptedClient([Completion("I cannot tell from here.", 1)])
    outcome = check(client)
    assert outcome.holds is None
    assert outcome.kind == INCONCLUSIVE
    assert len(client.asked) == 1


def test_an_answer_that_is_not_a_verdict_is_inconclusive() -> None:
    body = json.dumps(
        {"name": "mobile_use", "arguments": {"action": "click", "coordinate": [1, 2]}}
    )
    client = ScriptedClient([Completion(f"Action: tap\n<tool_call>{body}</tool_call>", 1)])
    outcome = check(client)
    assert outcome.kind == INCONCLUSIVE
    assert outcome.detail == "oracle did not return terminate"


def test_both_questions_are_asked_and_the_complement_is_the_second() -> None:
    client = ScriptedClient([verdict("fail"), verdict("success")])
    outcome = check(client)
    assert outcome.holds is False
    assert client.asked[0].startswith("Look only at the current screen.")
    assert "something other than" not in client.asked[0]
    assert "something other than" in client.asked[1]


def test_neither_question_is_a_double_negative() -> None:
    """The second one used to read "is it true that it is NOT the case that
    X", which asks for a negated proposition to be confirmed and then mapped
    back through "success means the negation is visible". Two inversions to
    get one bit out."""
    client = ScriptedClient([verdict("fail"), verdict("success")])
    check(client)
    for question in client.asked:
        assert "NOT the case" not in question
        assert "negation" not in question


def test_the_oracle_is_not_asked_under_the_actors_system_prompt() -> None:
    """The actor prompt ends "Do not terminate with status success unless the
    requested task is complete on the screen in front of you", which forbids
    the answer the second question needs. The system prompt wins, and that is
    the mechanism behind every (fail, fail) pair."""

    class PromptClient(ScriptedClient):
        def __init__(self, replies: list[Completion]) -> None:
            super().__init__(replies)
            self.systems: list[str] = []

        def complete(
            self, messages: list[dict[str, Any]], *, timeout_s: float | None = None
        ) -> Completion:
            self.systems.append(str(messages[0]["content"]))
            return super().complete(messages, timeout_s=timeout_s)

    client = PromptClient([verdict("fail"), verdict("success")])
    check(client)
    assert len(client.systems) == 2
    for system in client.systems:
        assert system == qwen_vl.ORACLE_SYSTEM_PROMPT
        assert "Do not terminate with status success unless" not in system
        assert "## Action space" not in system


def test_the_cost_of_a_verdict_reaches_the_artifact() -> None:
    client = ScriptedClient([failed(), verdict("success"), verdict("fail")])
    record = check(client).as_dict()
    assert record["kind"] == OK
    assert record["attempts"] == 3
    assert record["errors"] == [TIMEOUT]
    assert record["condition"] == "success"
    assert record["negation"] == "fail"
    assert record["screenshot"] == "turn_000.after.png"
    assert record["phase"] == "final"
    assert record["turn"] is None
