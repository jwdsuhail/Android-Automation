"""The console's HTTP surface.

Reading a runs directory, editing a cases directory, and starting one run at a
time, plus the built frontend. Every handler is a plain `def`: they do blocking
file I/O, which FastAPI runs on its threadpool, and nothing here is worth the
cost of being wrong about async.

This is no longer read-only. It writes `cases/`, it spawns `model-run`, and it
can delete a run directory. It still binds `127.0.0.1` and has no
authentication, which remains the whole of its threat model; `--no-run` removes
both of the endpoints that touch `runs/` - starting and deleting - for anyone
who wants the old guarantee back.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Iterator

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from android_runner.cases import Case, CaseStore, slugify
from android_runner.console.launcher import Busy, Launcher
from android_runner.console.reader import RunStore

STATIC = Path(__file__).parent / "static"

# Artifacts never change once written: turn_007.png is the screen the model saw
# on turn 7 and there will not be another one.
IMMUTABLE = "public, max-age=31536000, immutable"

# How often the event stream re-reads the run directory. Fast enough that a
# turn appears as it lands, slow enough that a 70-turn run is not re-parsed
# hundreds of times a minute.
POLL_S = 0.5

# A stream is capped rather than trusted to end. A wedged run that never writes
# `run.json` would otherwise hold a worker for as long as the console lives.
MAX_STREAM_S = 4 * 60 * 60

# A run this console did not start has no process to watch, so silence is the
# only signal that it died. Generous, because one slow turn can legitimately
# take MODEL_TIMEOUT_S; short enough that a dead run is not followed for hours.
IDLE_S = 5 * 60

_UNBUILT = """<!doctype html>
<title>Console not built</title>
<style>
  :root { color-scheme: dark }
  body { font: 15px/1.6 ui-monospace, monospace; background: #000; color: #ededed;
         margin: 0; display: grid; place-items: center; min-height: 100vh;
         padding-block: 2rem; padding-left: 1rem; padding-right: 1rem }
  div { max-width: 34rem }
  code { background: #1a1a1a; padding: .15em .4em; border-radius: 4px; color: #fff }
  p { color: #a1a1a1 }
</style>
<div>
  <h1>The frontend has not been built</h1>
  <p>The API is running and serving <code>/api/runs</code>. To build the interface:</p>
  <p><code>npm --prefix console install &amp;&amp; npm --prefix console run build</code></p>
</div>
"""


def _sse(event: str, payload: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def create_app(
    runs_dir: Path,
    cases_dir: Path | None = None,
    *,
    allow_run: bool = True,
) -> FastAPI:
    app = FastAPI(title="Android runner console", docs_url=None, redoc_url=None)
    runs_dir = Path(runs_dir)
    # `main` always passes `--cases-dir` (default `cases`), so this fallback is
    # for programmatic callers only and deliberately does not have to match the
    # flag default. Changing one to agree with the other would silently move
    # where an embedding caller reads cases from.
    cases_dir = Path(cases_dir) if cases_dir is not None else runs_dir.parent / "cases"
    store = RunStore(runs_dir)
    cases = CaseStore(cases_dir)
    launcher = Launcher(runs_dir, cases_dir) if allow_run else None

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "runs_dir": str(runs_dir.resolve()),
            "cases_dir": str(cases_dir.resolve()),
            "runs": len(store.ids()),
            "cases": len(cases.ids()),
            "built": (STATIC / "index.html").is_file(),
            "can_run": launcher is not None,
        }

    @app.get("/api/runs")
    def runs() -> list[dict[str, object]]:
        return [summary.as_dict() for summary in store.summaries()]

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str) -> dict[str, object]:
        detail = store.detail(run_id)
        if detail is None:
            raise HTTPException(404, f"no run named {run_id!r} in {runs_dir}")
        return detail.as_dict()

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: str) -> StreamingResponse:
        """Turns as they land, read from the run directory.

        Nothing parses the runner's output. `reader.py` already rebuilds a run
        from its `turn_*.json` files, so a run in progress is just an unfinished
        run - which means a run started at the terminal streams here too.
        """
        if store.summary(run_id) is None:
            raise HTTPException(404, f"no run named {run_id!r} in {runs_dir}")

        def stream() -> Iterator[str]:
            sent = 0
            started = time.monotonic()
            last_change = started
            deadline = started + MAX_STREAM_S
            while time.monotonic() < deadline:
                detail = store.detail(run_id)
                if detail is None:
                    yield _sse("end", {"reason": "the run directory disappeared"})
                    return

                payload = detail.as_dict()
                turns = payload["turns"]
                while sent < len(turns):
                    yield _sse("turn", turns[sent])
                    sent += 1
                    last_change = time.monotonic()
                # `started_at` rides along because a reattached watcher has
                # no idea when the run began, and an elapsed time counting from
                # the moment the page opened would be a lie.
                yield _sse(
                    "status",
                    {
                        key: payload[key]
                        for key in ("status", "outcome", "actions", "waits", "started_at")
                    },
                )

                if payload["complete"]:
                    yield _sse("end", payload)
                    return

                launch = launcher.status(run_id) if launcher else None
                if launch is None and time.monotonic() - last_change > IDLE_S:
                    # Nobody here is running this and it has stopped writing.
                    yield _sse(
                        "end",
                        {"reason": "the run stopped writing turns and is not one this console started"},
                    )
                    return
                if launch is not None and not launch.running:
                    # The process is gone and `run.json` never arrived: the run
                    # died before `finish()`. Say so rather than streaming until
                    # the cap, and point at the log that holds the reason.
                    time.sleep(POLL_S)
                    final = store.detail(run_id)
                    if final is not None and final.as_dict()["complete"]:
                        yield _sse("end", final.as_dict())
                        return
                    yield _sse(
                        "end",
                        {
                            "reason": "the runner exited without writing run.json",
                            "exit_code": launch.exit_code,
                            "log": "console.log",
                        },
                    )
                    return
                time.sleep(POLL_S)

            yield _sse("end", {"reason": "the stream reached its time limit"})

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/runs/{run_id}/files/{name}")
    def run_file(run_id: str, name: str) -> FileResponse:
        path = store.artifact(run_id, name)
        if path is None:
            raise HTTPException(404, f"no artifact named {name!r} in run {run_id!r}")
        # A run in progress rewrites neither, but it is still being written to,
        # so neither may be cached for a year the way a turn screenshot is.
        mutable = name.endswith(".json") or name.endswith(".log")
        headers = {} if mutable else {"Cache-Control": IMMUTABLE}
        return FileResponse(path, headers=headers)

    def _no_case(case_id: str) -> str:
        """One wording for a missing case, wherever the lookup happened.

        The three endpoints below reach for a case in three different ways
        (`get`, `exists`, `delete`), and a caller should not be able to tell
        which one it hit from the 404 it gets back.
        """
        return f"no case named {case_id!r} in {cases_dir}"

    def require_case(case_id: str) -> Case:
        """The case, or the right HTTPException. Never None."""
        try:
            case = cases.get(case_id)
        except ValueError as exc:
            raise HTTPException(422, f"{case_id} will not parse: {exc}") from exc
        if case is None:
            raise HTTPException(404, _no_case(case_id))
        return case

    # ---- cases -----------------------------------------------------------

    @app.get("/api/cases")
    def list_cases() -> dict[str, object]:
        return cases.list().as_dict()

    @app.post("/api/cases", status_code=201)
    def create_case(payload: dict[str, Any] = Body(...)) -> dict[str, object]:
        name = str(payload.get("name") or "").strip()
        base = slugify(name)
        if not base:
            raise HTTPException(
                422, "give the case a name containing letters or digits"
            )
        try:
            case = Case.from_dict(payload, id=cases.unique_id(base))
            return cases.save(case).as_dict()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/cases/{case_id}")
    def get_case(case_id: str) -> dict[str, object]:
        return require_case(case_id).as_dict()

    @app.put("/api/cases/{case_id}")
    def update_case(case_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, object]:
        if not cases.exists(case_id):
            raise HTTPException(404, _no_case(case_id))
        try:
            existing = cases.get(case_id)
        except ValueError:
            existing = None
        try:
            # The id never follows the name. A renamed case keeps its address,
            # and every run that recorded it still points at something.
            merged = dict(payload)
            merged["created_at"] = existing.created_at if existing else ""
            return cases.save(Case.from_dict(merged, id=case_id)).as_dict()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/cases/{case_id}", status_code=204)
    def delete_case(case_id: str) -> None:
        if not cases.delete(case_id):
            raise HTTPException(404, _no_case(case_id))

    @app.get("/api/active")
    def active() -> dict[str, object]:
        launch = launcher.active() if launcher else None
        return {"active": launch.as_dict() if launch else None}

    if launcher is not None:

        @app.post("/api/cases/{case_id}/run", status_code=202)
        def run_case(case_id: str) -> JSONResponse:
            case = require_case(case_id)
            try:
                launch = launcher.launch(case)
            except Busy as exc:
                # 409, not 503: this is a conflict with a specific run, and the
                # response names it so the caller can go and watch that one.
                raise HTTPException(409, str(exc)) from exc
            except OSError as exc:
                raise HTTPException(500, f"could not start the runner: {exc}") from exc
            return JSONResponse(launch.as_dict(), status_code=202)

        @app.delete("/api/runs/{run_id}", status_code=204)
        def delete_run(run_id: str) -> None:
            """Remove a run directory. There is no trash and no undo.

            Deleting one run at a time rather than taking a list keeps the
            refusal below attached to the run it is about: a caller clearing
            out ten runs is told which one was kept, not that the batch
            failed.
            """
            launch = launcher.active()
            if launch is not None and launch.run_id == run_id:
                # The process is still writing turn files into that directory.
                # Removing it underneath them turns a live run into a spray of
                # errors from somewhere that will not name this as the cause.
                raise HTTPException(
                    409,
                    f"{run_id} is still running. Let it finish, or stop the "
                    "process, before deleting it.",
                )
            if not store.delete(run_id):
                raise HTTPException(404, f"no run named {run_id!r} in {runs_dir}")

    # ---- the frontend, which must stay last ------------------------------
    # The catch-all below answers to every path. Any route registered after it
    # is unreachable.

    if (STATIC / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

        @app.get("/{path:path}", response_class=FileResponse)
        def app_shell(path: str) -> FileResponse:
            """A built file when there is one, otherwise the app itself.

            `/runs/20260914T121427Z?turn=11` is an address worth sending to
            someone, so it has to survive a reload. Nothing on disk answers to
            it: only the browser knows how to render that run, so every path
            the build did not produce returns the shell and lets it route.
            """
            if path.startswith("api/"):
                raise HTTPException(404, f"no endpoint at /{path}")
            candidate = (STATIC / path).resolve()
            if path and candidate.is_file() and candidate.is_relative_to(STATIC.resolve()):
                return FileResponse(candidate)
            return FileResponse(STATIC / "index.html")
    else:

        @app.get("/{path:path}", response_class=HTMLResponse)
        def unbuilt(path: str) -> str:
            if path.startswith("api/"):
                raise HTTPException(404, f"no endpoint at /{path}")
            return _UNBUILT

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the run console.")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--cases-dir", type=Path, default=Path("cases"))
    parser.add_argument(
        "--no-run",
        action="store_true",
        help="Refuse to start or delete a run, leaving runs/ read-only. Cases "
        "stay editable.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    import uvicorn

    app = create_app(args.runs_dir, args.cases_dir, allow_run=not args.no_run)
    print(f"console  http://{args.host}:{args.port}")
    print(f"runs     {args.runs_dir.resolve()}{'  (read-only)' if args.no_run else ''}")
    print(f"cases    {args.cases_dir.resolve()}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
