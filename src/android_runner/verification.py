"""Fresh-screen oracle verification, independent from the actor's conversation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from android_runner import qwen_vl
from android_runner.client import ModelClient


@dataclass(frozen=True)
class Check:
    holds: bool | None
    detail: str
    raw: str
    negated_raw: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "holds": self.holds,
            "detail": self.detail,
            "raw": self.raw[:1200],
            "negated_raw": self.negated_raw[:1200],
        }


def _ask(
    client: ModelClient,
    png: bytes,
    question: str,
    history_n: int,
    timeout_s: float,
) -> tuple[bool | None, str, str]:
    completion = client.complete(
        qwen_vl.build_messages(
            question, png, [], history_n, thinking=False, reflection=False
        ),
        timeout_s=timeout_s,
    )
    if completion.error:
        return None, completion.raw, completion.error
    try:
        parsed = qwen_vl.parse(completion.raw, completion.reasoning)
    except ValueError as exc:
        return None, completion.raw, str(exc)
    if parsed.action != "terminate":
        return None, completion.raw, "oracle did not return terminate"
    status = str(parsed.arguments.get("status", "")).lower()
    if status not in {"success", "fail"}:
        return None, completion.raw, f"invalid oracle status {status!r}"
    return status == "success", completion.raw, ""


def verify(
    client: ModelClient,
    png: bytes,
    success: str,
    history_n: int,
    timeout_s: float,
) -> Check:
    """Ask the predicate and its negation using fresh, history-free contexts."""
    question = (
        "Look only at the current screen. Is it true that "
        f"{success}? Return mobile_use terminate with status success if visible "
        "now, otherwise status fail. Do not perform another action."
    )
    negation = (
        "Look only at the current screen. Is it true that it is NOT the case that "
        f"{success}? Return mobile_use terminate with status success if that "
        "negation is visible now, otherwise status fail. Do not perform an action."
    )
    holds, raw, error = _ask(client, png, question, history_n, timeout_s)
    if holds is None:
        return Check(None, error, raw)

    negated, negated_raw, negated_error = _ask(
        client, png, negation, history_n, timeout_s
    )
    if negated is None:
        return Check(None, f"negation check failed: {negated_error}", raw, negated_raw)
    if holds == negated:
        return Check(
            None,
            "oracle gave the same answer to the predicate and its negation",
            raw,
            negated_raw,
        )
    return Check(holds, "predicate and negation answers were consistent", raw, negated_raw)
