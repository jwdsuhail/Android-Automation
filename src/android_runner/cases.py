"""A test case, stored as one JSON file.

A case is not a new abstraction over the runner. It is exactly the argument set
`model-run` already takes - an instruction, a success condition, and the budgets
that bound the loop. Giving that set a name and a file is the whole of this
module, so that the same test can be the same test twice.

The filesystem is the source of truth, as it is for `runs/`: one file per case,
git-diffable, with no index that can fall out of step with the directory.

The validation here is the *only* validation. `cli.py` and the console's HTTP
surface both call it, so a case the browser rejects is rejected at the terminal
for the same reason and in the same words.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# A case id is also a filename, so it is restricted to what is safe in one on
# every platform, and checked rather than sanitised when it arrives from HTTP.
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_MAX_SLUG = 64


def _now() -> str:
    """UTC, to the microsecond.

    `updated_at` is what the listing sorts on, and three calls fit inside one
    millisecond here, so anything coarser makes the order of two cases saved
    together arbitrary.
    """
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def slugify(name: str) -> str:
    """A filename-safe id from a human name.

    Returns an empty string when the name carries nothing usable - all
    punctuation, or all non-Latin script. The caller decides what to do about
    that, because "name your case something else" is a better error than a file
    called `case-1`.
    """
    lowered = re.sub(r"[^a-z0-9]+", "-", name.strip().lower())
    return lowered.strip("-")[:_MAX_SLUG].strip("-")


@dataclass(frozen=True)
class Case:
    """One runnable task. Field-for-field, the arguments of `model-run`."""

    id: str
    name: str
    instruction: str
    success: str | None = None
    max_actions: int = 20
    max_waits: int = 12
    wall_clock_s: float = 240.0
    # None gives the oracle the same ceiling as the actor, matching Budget.
    verify_timeout_s: float | None = None
    warmup: bool = True
    created_at: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        """Raise ValueError on anything the runner could not honour.

        The budget bounds are the ones `cli.py` used to inline. They live here
        now so that both entry points enforce one rule.
        """
        if not SLUG.match(self.id):
            raise ValueError(
                f"case id {self.id!r} must be lowercase letters, digits and single hyphens"
            )
        if not self.name.strip():
            raise ValueError("name must not be empty")
        if not self.instruction.strip():
            raise ValueError("instruction must not be empty")
        # An empty success condition is not the same as no success condition.
        # The first is a typo; the second is a deliberate exploration run.
        if self.success is not None and not self.success.strip():
            raise ValueError(
                "success must either describe a visible condition or be omitted "
                "entirely for an exploration run"
            )
        if self.max_actions < 1:
            raise ValueError("max_actions must be at least 1")
        if self.max_waits < 1:
            raise ValueError("max_waits must be at least 1")
        if self.wall_clock_s <= 0:
            raise ValueError("wall_clock_s must be greater than 0")
        if self.verify_timeout_s is not None and self.verify_timeout_s <= 0:
            raise ValueError("verify_timeout_s must be greater than 0")

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, id: str | None = None) -> Case:
        """Build a case from JSON, validated.

        Unknown keys are ignored rather than rejected, so a file written by a
        later version still loads here. Known keys are coerced, because a
        `max_actions` of `"20"` from a form post is a string that means twenty.
        """
        if not isinstance(payload, dict):
            raise ValueError("a case must be a JSON object")

        name = str(payload.get("name") or "").strip()
        case_id = id or str(payload.get("id") or "") or slugify(name)
        if not case_id:
            raise ValueError(
                f"cannot derive an id from name {name!r}; give the case a name "
                "containing letters or digits"
            )

        success = payload.get("success")
        verify = payload.get("verify_timeout_s")

        case = cls(
            id=case_id,
            name=name,
            instruction=str(payload.get("instruction") or "").strip(),
            success=None if success is None else str(success),
            max_actions=_int(payload, "max_actions", 20),
            max_waits=_int(payload, "max_waits", 12),
            wall_clock_s=_float(payload, "wall_clock_s", 240.0),
            verify_timeout_s=None if verify is None else _float(payload, "verify_timeout_s", 0.0),
            warmup=bool(payload.get("warmup", True)),
            created_at=str(payload.get("created_at") or "") or _now(),
            updated_at=str(payload.get("updated_at") or "") or _now(),
        )
        case.validate()
        return case


def _int(payload: dict[str, Any], key: str, fallback: int) -> int:
    value = payload.get(key, fallback)
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a whole number, not {value!r}") from exc


def _float(payload: dict[str, Any], key: str, fallback: float) -> float:
    value = payload.get(key, fallback)
    if value is None:
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a number, not {value!r}") from exc


@dataclass(frozen=True)
class Broken:
    """A file in the cases directory that would not parse.

    It is reported rather than skipped, for the same reason `reader.py` rebuilds
    a run that never finished: a case you cannot see is one you cannot fix.
    """

    id: str
    error: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Listing:
    cases: list[Case] = field(default_factory=list)
    broken: list[Broken] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cases": [case.as_dict() for case in self.cases],
            "broken": [bad.as_dict() for bad in self.broken],
        }


class CaseStore:
    """The cases directory, read and written as data."""

    def __init__(self, cases_dir: Path) -> None:
        self.dir = Path(cases_dir)

    def _path(self, case_id: str) -> Path:
        # Checked, never joined blind: `id` arrives from the URL.
        if not SLUG.match(case_id):
            raise ValueError(f"not a case id: {case_id!r}")
        return self.dir / f"{case_id}.json"

    def ids(self) -> list[str]:
        if not self.dir.is_dir():
            return []
        return sorted(path.stem for path in self.dir.glob("*.json"))

    def get(self, case_id: str) -> Case | None:
        try:
            path = self._path(case_id)
        except ValueError:
            return None
        if not path.is_file():
            return None
        return Case.from_dict(json.loads(path.read_text(encoding="utf-8")), id=case_id)

    def list(self) -> Listing:
        """Every case, newest edit first, with the unparseable ones named."""
        cases: list[Case] = []
        broken: list[Broken] = []
        for case_id in self.ids():
            try:
                case = self.get(case_id)
            except (OSError, ValueError) as exc:
                broken.append(Broken(id=case_id, error=str(exc)))
                continue
            if case is not None:
                cases.append(case)
        # Stable, so cases sharing a timestamp stay in id order rather than
        # swapping places between two reads of the same directory.
        cases.sort(key=lambda c: c.updated_at, reverse=True)
        return Listing(cases=cases, broken=broken)

    def exists(self, case_id: str) -> bool:
        try:
            return self._path(case_id).is_file()
        except ValueError:
            return False

    def unique_id(self, base: str) -> str:
        """`base`, or the first free `base-2`, `base-3`, ... after it."""
        if not self.exists(base):
            return base
        for suffix in range(2, 1000):
            candidate = f"{base}-{suffix}"[:_MAX_SLUG].strip("-")
            if not self.exists(candidate):
                return candidate
        raise ValueError(f"too many cases named like {base!r}")

    def save(self, case: Case) -> Case:
        """Write the case, stamping `updated_at`. Returns what was written.

        Written to a temporary file and moved into place: a case is read by the
        launcher moments after it is saved, and a half-written one is worse than
        an unsaved one.
        """
        case.validate()
        stamped = replace(case, updated_at=_now(), created_at=case.created_at or _now())
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(stamped.id)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(stamped.as_dict(), indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
        return stamped

    def delete(self, case_id: str) -> bool:
        try:
            path = self._path(case_id)
        except ValueError:
            return False
        if not path.is_file():
            return False
        path.unlink()
        return True
