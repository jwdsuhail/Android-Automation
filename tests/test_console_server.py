"""The console's HTTP surface, over a runs directory built for the test."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from android_runner.console.launcher import Launch, Launcher
from android_runner.console.server import create_app
from test_console_reader import CLICK, WAIT, complete_run, write_turns


def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path))


def test_health_reports_where_it_is_reading_from(tmp_path: Path) -> None:
    complete_run(tmp_path, "20260914T121427Z")
    body = client(tmp_path).get("/api/health").json()
    assert body["runs"] == 1
    assert body["runs_dir"] == str(tmp_path.resolve())


def test_the_listing_includes_runs_that_never_finished(tmp_path: Path) -> None:
    complete_run(tmp_path, "20260914T121427Z")
    write_turns(tmp_path / "20260908T164612Z", [CLICK, WAIT])

    rows = client(tmp_path).get("/api/runs").json()
    assert [r["id"] for r in rows] == ["20260914T121427Z", "20260908T164612Z"]
    assert [r["outcome"] for r in rows] == ["pass", "incomplete"]


def test_detail_returns_the_turns_with_their_rendered_line(tmp_path: Path) -> None:
    complete_run(tmp_path, "20260914T121427Z")
    body = client(tmp_path).get("/api/runs/20260914T121427Z").json()
    assert body["instruction"] == "Open FYI"
    assert body["turns"][0]["line"] == (
        "click 228,640 of 1000 [mid-left], px 246,1551 moved"
    )


def test_an_unknown_run_is_a_clean_404(tmp_path: Path) -> None:
    response = client(tmp_path).get("/api/runs/20991231T000000Z")
    assert response.status_code == 404
    assert "20991231T000000Z" in response.json()["detail"]


def test_an_artifact_is_served_with_its_own_bytes(tmp_path: Path) -> None:
    run_dir = complete_run(tmp_path, "20260914T121427Z")
    (run_dir / "turn_000.marked.png").write_bytes(b"\x89PNG\r\n\x1a\npretend")

    response = client(tmp_path).get(
        "/api/runs/20260914T121427Z/files/turn_000.marked.png"
    )
    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG")
    assert "immutable" in response.headers["cache-control"]


def test_a_name_outside_the_run_directory_is_refused(tmp_path: Path) -> None:
    complete_run(tmp_path, "20260914T121427Z")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    api = client(tmp_path)

    for name in ("../secret.txt", "..%2fsecret.txt", "%2e%2e%2fsecret.txt", "notes.txt"):
        response = api.get(f"/api/runs/20260914T121427Z/files/{name}")
        assert response.status_code == 404, name
        assert b"no" != response.content


def test_the_page_says_how_to_build_it_when_the_frontend_is_missing(
    tmp_path: Path,
) -> None:
    """An error that does not say how to fix it is a dead end."""
    from android_runner.console import server

    if (server.STATIC / "index.html").is_file():
        return  # the frontend is built; this state cannot be reached
    body = client(tmp_path).get("/").text
    assert "npm --prefix console run build" in body


def test_a_deep_link_to_a_run_serves_the_app_shell(tmp_path: Path) -> None:
    """`/runs/<id>?turn=11` is only a link if it survives a reload."""
    from android_runner.console import server

    if not (server.STATIC / "index.html").is_file():
        return  # the frontend is not built; this state cannot be reached
    response = client(tmp_path).get("/runs/20260914T121427Z?turn=11")
    assert response.status_code == 200
    assert 'id="root"' in response.text


def test_an_unknown_api_path_is_a_404_and_not_the_app_shell(tmp_path: Path) -> None:
    """A typo in a fetch must fail loudly, not arrive as a page of HTML."""
    response = client(tmp_path).get("/api/nope")
    assert response.status_code == 404
    assert "html" not in response.headers["content-type"]


# --- deleting a run -------------------------------------------------------


def test_deleting_a_run_removes_the_directory_and_the_listing_entry(
    tmp_path: Path,
) -> None:
    complete_run(tmp_path, "20260914T121427Z")
    complete_run(tmp_path, "20260908T164612Z")
    api = client(tmp_path)

    assert api.delete("/api/runs/20260908T164612Z").status_code == 204
    assert not (tmp_path / "20260908T164612Z").exists()
    assert [r["id"] for r in api.get("/api/runs").json()] == ["20260914T121427Z"]


def test_deleting_an_unknown_run_is_a_clean_404(tmp_path: Path) -> None:
    response = client(tmp_path).delete("/api/runs/20991231T000000Z")
    assert response.status_code == 404
    assert "20991231T000000Z" in response.json()["detail"]


def test_a_run_still_writing_is_refused_rather_than_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing the directory would break the process still writing into it."""
    complete_run(tmp_path, "20260914T121427Z")
    live = Launch(
        run_id="20260914T121427Z",
        case_id="audio-metadata",
        started_at="2026-09-14T12:14:27Z",
        pid=4242,
    )
    monkeypatch.setattr(Launcher, "active", lambda self: live)

    response = client(tmp_path).delete("/api/runs/20260914T121427Z")
    assert response.status_code == 409
    assert "still running" in response.json()["detail"]
    assert (tmp_path / "20260914T121427Z").is_dir()


def test_no_run_leaves_runs_read_only(tmp_path: Path) -> None:
    """`--no-run` covers both endpoints that write to runs/, not just launching."""
    complete_run(tmp_path, "20260914T121427Z")
    api = TestClient(create_app(tmp_path, tmp_path / "cases", allow_run=False))

    # No route is registered, so the app shell answers instead - the point is
    # that nothing deletes, and the run is still on disk afterwards.
    assert api.delete("/api/runs/20260914T121427Z").status_code != 204
    assert (tmp_path / "20260914T121427Z").is_dir()
    assert api.get("/api/health").json()["can_run"] is False
