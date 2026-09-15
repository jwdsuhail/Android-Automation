"""Fresh-screen oracle verification, independent from the actor's conversation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from android_runner import qwen_vl
from android_runner.client import ModelClient

# A verdict never arrived because the transport failed. Says nothing about the
# agent, so the run that carries it is not a measurement of one.
INFRASTRUCTURE = "infrastructure"
# A reply arrived and was unusable: not a terminate, an unknown status, or the
# same answer to the predicate and its negation. The judge, not the network.
INCONCLUSIVE = "inconclusive"
OK = "ok"


def _label(holds: bool | None) -> str | None:
    """success/fail for a yes/no answer; None when that question never landed."""
    if holds is True:
        return "success"
    if holds is False:
        return "fail"
    return None


@dataclass(frozen=True)
class Check:
    holds: bool | None
    detail: str
    raw: str
    negated_raw: str = ""
    # Which of the three above. Only meaningful when `holds` is None; a real
    # verdict is always OK.
    kind: str = OK
    # How many calls the verdict cost and what the discarded attempts said.
    # Kept so a run that needed three tries is visibly different from one that
    # needed one, rather than both reading as a clean pass.
    attempts: int = 1
    errors: tuple[str, ...] = ()
    # The two answers themselves. `holds` is the verdict after pairing them;
    # these stay even when that pairing is inconclusive, so the terminal can
    # show "both said fail" instead of only the pairing error.
    condition: str | None = None
    negation: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "holds": self.holds,
            "detail": self.detail,
            "kind": self.kind,
            "attempts": self.attempts,
            "errors": list(self.errors),
            "condition": self.condition,
            "negation": self.negation,
            "raw": self.raw[:1200],
            "negated_raw": self.negated_raw[:1200],
        }


@dataclass(frozen=True)
class _Answer:
    """One question's outcome, before it is paired with its negation."""

    holds: bool | None
    raw: str
    detail: str
    kind: str
    attempts: int
    errors: tuple[str, ...]


def _ask(
    client: ModelClient,
    png: bytes,
    question: str,
    history_n: int,
    timeout_s: float,
    attempts: int,
) -> _Answer:
    """Put one question to a fresh context, retrying only a failed transport.

    A reply that arrived but parsed badly is not retried: temperature is 0, so
    the second call returns the first call's answer. A verdict is never retried
    at all - asking again until the answer changes is best-of-N dressed up as
    verification.
    """
    messages = qwen_vl.build_messages(
        question, png, [], history_n, thinking=False, reflection=False
    )
    errors: list[str] = []
    raw = ""
    for attempt in range(1, max(1, attempts) + 1):
        completion = client.complete(messages, timeout_s=timeout_s)
        raw = completion.raw
        if completion.error:
            errors.append(completion.error)
            continue
        try:
            parsed = qwen_vl.parse(completion.raw, completion.reasoning)
        except ValueError as exc:
            return _Answer(None, raw, str(exc), INCONCLUSIVE, attempt, tuple(errors))
        if parsed.action != "terminate":
            return _Answer(
                None,
                raw,
                "oracle did not return terminate",
                INCONCLUSIVE,
                attempt,
                tuple(errors),
            )
        status = str(parsed.arguments.get("status", "")).lower()
        if status not in {"success", "fail"}:
            return _Answer(
                None,
                raw,
                f"invalid oracle status {status!r}",
                INCONCLUSIVE,
                attempt,
                tuple(errors),
            )
        return _Answer(status == "success", raw, "", OK, attempt, tuple(errors))
    return _Answer(
        None, raw, errors[-1], INFRASTRUCTURE, max(1, attempts), tuple(errors)
    )


def verify(
    client: ModelClient,
    png: bytes,
    success: str,
    history_n: int,
    timeout_s: float,
    attempts: int = 2,
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
    first = _ask(client, png, question, history_n, timeout_s, attempts)
    if first.holds is None:
        return Check(
            None,
            first.detail,
            first.raw,
            kind=first.kind,
            attempts=first.attempts,
            errors=first.errors,
            condition=_label(first.holds),
        )

    second = _ask(client, png, negation, history_n, timeout_s, attempts)
    spent = first.attempts + second.attempts
    errors = first.errors + second.errors
    if second.holds is None:
        return Check(
            None,
            f"negation check failed: {second.detail}",
            first.raw,
            second.raw,
            kind=second.kind,
            attempts=spent,
            errors=errors,
            condition=_label(first.holds),
            negation=_label(second.holds),
        )
    if first.holds == second.holds:
        return Check(
            None,
            "oracle gave the same answer to the predicate and its negation",
            first.raw,
            second.raw,
            kind=INCONCLUSIVE,
            attempts=spent,
            errors=errors,
            condition=_label(first.holds),
            negation=_label(second.holds),
        )
    return Check(
        first.holds,
        "predicate and negation answers were consistent",
        first.raw,
        second.raw,
        kind=OK,
        attempts=spent,
        errors=errors,
        condition=_label(first.holds),
        negation=_label(second.holds),
    )
