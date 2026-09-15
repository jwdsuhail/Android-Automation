"""The console's HTTP surface.

Four endpoints over a runs directory, plus the built frontend. Every handler
is a plain `def`: they do blocking file I/O, which FastAPI runs on its
threadpool, and nothing here is worth the cost of being wrong about async.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from android_runner.console.reader import RunStore

STATIC = Path(__file__).parent / "static"

# Artifacts never change once written: turn_007.png is the screen the model saw
# on turn 7 and there will not be another one.
IMMUTABLE = "public, max-age=31536000, immutable"

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


def create_app(runs_dir: Path) -> FastAPI:
    app = FastAPI(title="Android runner console", docs_url=None, redoc_url=None)
    store = RunStore(runs_dir)

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "runs_dir": str(runs_dir.resolve()),
            "runs": len(store.ids()),
            "built": (STATIC / "index.html").is_file(),
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

    @app.get("/api/runs/{run_id}/files/{name}")
    def run_file(run_id: str, name: str) -> FileResponse:
        path = store.artifact(run_id, name)
        if path is None:
            raise HTTPException(404, f"no artifact named {name!r} in run {run_id!r}")
        headers = {} if name.endswith(".json") else {"Cache-Control": IMMUTABLE}
        return FileResponse(path, headers=headers)

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
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    import uvicorn

    print(f"console  http://{args.host}:{args.port}")
    print(f"runs     {args.runs_dir.resolve()}")
    uvicorn.run(create_app(args.runs_dir), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
