import { useEffect, useState } from "react";
import { getRun, listRuns, type Outcome, type RunDetail as Detail, type RunSummary } from "./api";
import { RunDetail } from "./components/RunDetail";
import { RunList } from "./components/RunList";
import { TurnDetail } from "./components/TurnDetail";
import { useRoute } from "./useUrlState";

function Placeholder({ title, body }: { title: string; body: string }) {
  return (
    <div className="grid h-full place-items-center px-6 py-10">
      <div className="max-w-sm text-center">
        <p className="text-[13px] text-text">{title}</p>
        <p className="mt-1.5 text-[12px] leading-relaxed text-dim">{body}</p>
      </div>
    </div>
  );
}

export default function App() {
  const [route, navigate] = useRoute();
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [filter, setFilter] = useState<Outcome | "all">("all");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listRuns().then(setRuns).catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    if (route.runId === null) {
      setDetail(null);
      return;
    }
    let live = true;
    setDetail(null);
    getRun(route.runId)
      .then((found) => live && setDetail(found))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [route.runId]);

  // A run with no turn chosen leaves the right column empty, which reads as
  // broken rather than as a prompt. Land on the first turn instead, and say
  // so in the URL so the address still describes the screen.
  useEffect(() => {
    if (detail && route.turn === null && detail.turns.length > 0) {
      navigate({ runId: detail.id, turn: detail.turns[0].index }, true);
    }
  }, [detail, route.turn, navigate]);

  useEffect(() => {
    document.title = route.runId ? `${route.runId} - Runs` : "Runs";
  }, [route.runId]);

  const turn =
    detail?.turns.find((t) => t.index === route.turn) ?? null;

  if (error !== null) {
    return (
      <Placeholder
        title="The console could not reach its API."
        body={`${error}. Start the server with model-console, then reload.`}
      />
    );
  }

  return (
    <div className="grid h-screen grid-cols-1 bg-canvas lg:grid-cols-[264px_minmax(0,1fr)_372px]">
      {/* Below the breakpoint there is room for one panel, not three. Stacking
          them would bury the chosen run under twenty-two rows of list, so the
          list steps aside once a run is picked and the header offers the way
          back. */}
      <div
        className={`min-h-0 overflow-hidden ${route.runId ? "hidden lg:block" : "block"}`}
      >
        <RunList
          runs={runs ?? []}
          selected={route.runId}
          filter={filter}
          onFilter={setFilter}
          onSelect={(id) => navigate({ runId: id, turn: null })}
        />
      </div>

      <main
        className={`min-w-0 overflow-hidden ${route.runId ? "block" : "hidden lg:block"}`}
      >
        {detail ? (
          <RunDetail
            run={detail}
            selectedTurn={route.turn}
            onSelectTurn={(index) => navigate({ runId: detail.id, turn: index })}
            onBack={() => navigate({ runId: null, turn: null })}
          />
        ) : route.runId ? (
          <Placeholder title="Loading the run." body="Reading its turn records." />
        ) : (
          <Placeholder
            title="Pick a run."
            body={
              runs && runs.length === 0
                ? "There are no runs in this directory yet. Start one with model-run."
                : "Every run in the runs directory is on the left, including the ones that did not finish."
            }
          />
        )}
      </main>

      <div className="hidden min-w-0 lg:block">
        {detail && turn ? (
          <TurnDetail runId={detail.id} turn={turn} artifacts={detail.artifacts} />
        ) : (
          <aside className="h-full border-l border-border bg-panel">
            {detail && detail.turns.length === 0 && (
              <Placeholder
                title="No turns to show."
                body="This run ended before the first model call."
              />
            )}
          </aside>
        )}
      </div>
    </div>
  );
}
