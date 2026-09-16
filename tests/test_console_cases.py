"""The console's case endpoints, and the one-run-at-a-time refusal."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from android_runner.cases import Case
from android_runner.console.launcher import Busy, Launch
from android_runner.console.server import create_app

BODY: dict[str, Any] = {
    "name": "Audio metadata",
    "instruction": "Open the Test 14 chat and update the audio metadata",
    "success": "the audio message is labelled Verified Track",
    "max_actions": 70,
}


def client(tmp_path: Path, **kwargs: Any) -> TestClient:
    return TestClient(create_app(tmp_path / "runs", tmp_path / "cases", **kwargs))


def test_a_case_survives_create_and_read_back(tmp_path: Path) -> None:
    api = client(tmp_path)
    created = api.post("/api/cases", json=BODY)
    assert created.status_code == 201
    assert created.json()["id"] == "audio-metadata"
    assert created.json()["max_actions"] == 70

    fetched = api.get("/api/cases/audio-metadata")
    assert fetched.status_code == 200
    assert fetched.json()["instruction"] == BODY["instruction"]


def test_the_listing_reports_cases_and_the_files_that_would_not_parse(tmp_path: Path) -> None:
    api = client(tmp_path)
    api.post("/api/cases", json=BODY)
    (tmp_path / "cases" / "broken.json").write_text("{nope", encoding="utf-8")

    body = api.get("/api/cases").json()
    assert [c["id"] for c in body["cases"]] == ["audio-metadata"]
    assert [b["id"] for b in body["broken"]] == ["broken"]


def test_two_cases_with_one_name_do_not_overwrite_each_other(tmp_path: Path) -> None:
    api = client(tmp_path)
    first = api.post("/api/cases", json=BODY).json()
    second = api.post("/api/cases", json=BODY).json()
    assert first["id"] == "audio-metadata"
    assert second["id"] == "audio-metadata-2"


def test_a_rename_keeps_the_id_so_old_links_still_resolve(tmp_path: Path) -> None:
    """Runs record the id they ran. Moving it would orphan every one of them."""
    api = client(tmp_path)
    api.post("/api/cases", json=BODY)
    updated = api.put(
        "/api/cases/audio-metadata", json={**BODY, "name": "Something else entirely"}
    )
    assert updated.status_code == 200
    assert updated.json()["id"] == "audio-metadata"
    assert updated.json()["name"] == "Something else entirely"


def test_an_update_preserves_when_the_case_was_created(tmp_path: Path) -> None:
    api = client(tmp_path)
    created = api.post("/api/cases", json=BODY).json()
    updated = api.put("/api/cases/audio-metadata", json={**BODY, "name": "New name"}).json()
    assert updated["created_at"] == created["created_at"]
    assert updated["updated_at"] >= created["updated_at"]


@pytest.mark.parametrize(
    "payload, fragment",
    [
        ({"name": "x", "instruction": ""}, "instruction must not be empty"),
        ({"name": "x", "instruction": "y", "max_actions": 0}, "max_actions must be at least 1"),
        ({"name": "!!!", "instruction": "y"}, "letters or digits"),
    ],
)
def test_a_case_the_runner_could_not_honour_is_refused(
    tmp_path: Path, payload: dict[str, Any], fragment: str
) -> None:
    """The same messages `Case.validate()` gives at the terminal."""
    response = client(tmp_path).post("/api/cases", json=payload)
    assert response.status_code == 422
    assert fragment in response.json()["detail"]


def test_missing_cases_are_404_not_500(tmp_path: Path) -> None:
    api = client(tmp_path)
    assert api.get("/api/cases/nope").status_code == 404
    assert api.put("/api/cases/nope", json=BODY).status_code == 404
    assert api.delete("/api/cases/nope").status_code == 404
    assert api.post("/api/cases/nope/run").status_code == 404


def test_delete_removes_the_file(tmp_path: Path) -> None:
    api = client(tmp_path)
    api.post("/api/cases", json=BODY)
    assert api.delete("/api/cases/audio-metadata").status_code == 204
    assert not (tmp_path / "cases" / "audio-metadata.json").is_file()
    assert api.get("/api/cases/audio-metadata").status_code == 404


def test_health_says_where_cases_live_and_whether_it_can_run(tmp_path: Path) -> None:
    body = client(tmp_path).get("/api/health").json()
    assert body["cases_dir"] == str((tmp_path / "cases").resolve())
    assert body["can_run"] is True

    read_only = client(tmp_path, allow_run=False).get("/api/health").json()
    assert read_only["can_run"] is False


def test_no_run_removes_the_way_to_start_one(tmp_path: Path) -> None:
    """`--no-run` restores the console's original read-only guarantee."""
    api = client(tmp_path, allow_run=False)
    api.post("/api/cases", json=BODY)
    # The route is never registered, so the app shell answers instead.
    assert api.post("/api/cases/audio-metadata/run").status_code in (404, 405)
    assert api.get("/api/active").json()["active"] is None


def test_a_second_run_is_refused_while_one_is_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One emulator cannot run two tests: they would act on each other's screen."""
    app = create_app(tmp_path / "runs", tmp_path / "cases")
    api = TestClient(app)
    api.post("/api/cases", json=BODY)

    started: list[Launch] = []

    def fake_launch(self: Any, case: Case) -> Launch:
        if started:
            raise Busy(started[0].run_id)
        launch = Launch(
            run_id="20260915T120000Z",
            case_id=case.id,
            started_at="2026-09-15T12:00:00Z",
            pid=4242,
        )
        started.append(launch)
        return launch

    monkeypatch.setattr("android_runner.console.launcher.Launcher.launch", fake_launch)

    first = api.post("/api/cases/audio-metadata/run")
    assert first.status_code == 202
    assert first.json()["run_id"] == "20260915T120000Z"

    second = api.post("/api/cases/audio-metadata/run")
    assert second.status_code == 409
    assert "20260915T120000Z" in second.json()["detail"]


# --- the event stream -----------------------------------------------------


def _events(raw: str) -> list[tuple[str, Any]]:
    """Parse an SSE body into (event, data) pairs."""
    import json as _json

    out: list[tuple[str, Any]] = []
    for block in raw.strip().split("\n\n"):
        if not block.strip():
            continue
        name = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = _json.loads(line[6:])
        if name is not None:
            out.append((name, data))
    return out


def test_the_stream_replays_a_finished_run_then_ends(tmp_path: Path) -> None:
    """A completed run streams everything it has and closes, rather than
    polling for turns that will never arrive."""
    from test_console_reader import CLICK, WAIT, complete_run

    runs = tmp_path / "runs"
    complete_run(runs, "20260914T121427Z", turns=[CLICK, WAIT])
    api = client(tmp_path)

    with api.stream("GET", "/api/runs/20260914T121427Z/events") as response:
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = "".join(response.iter_text())

    events = _events(body)
    assert [name for name, _ in events] == ["turn", "turn", "status", "end"]
    assert events[0][1]["line"].startswith("click 228,640")
    assert events[-1][1]["outcome"] == "pass"
    # A watcher that arrives late has no other way to know when the run began,
    # and an elapsed time counting from page load would be wrong.
    assert events[2][1]["started_at"] == "2026-09-14T12:14:27Z"


def test_the_stream_404s_for_a_run_that_does_not_exist(tmp_path: Path) -> None:
    assert client(tmp_path).get("/api/runs/nope/events").status_code == 404
