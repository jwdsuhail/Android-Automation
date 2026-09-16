import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  createCase,
  deleteCase,
  deleteRun,
  getActive,
  getHealth,
  getRun,
  listCases,
  listRuns,
  runCase,
  updateCase,
  type Case,
  type CaseDraft,
  type CaseListing,
  type Launch,
  type Outcome,
  type RunDetail as Detail,
  type RunSummary,
} from "./api";
import { CaseDetail } from "./components/CaseDetail";
import { CaseForm } from "./components/CaseForm";
import { CaseList } from "./components/CaseList";
import { LiveRun } from "./components/LiveRun";
import { RunDetail } from "./components/RunDetail";
import { RunList } from "./components/RunList";
import { TurnDetail } from "./components/TurnDetail";
import { ViewSwitch } from "./components/ViewSwitch";
import { HOME, useRoute, type Route } from "./useUrlState";

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

  const [cases, setCases] = useState<CaseListing | null>(null);
  const [canRun, setCanRun] = useState(false);
  const [active, setActive] = useState<Launch | null>(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  // Set only by pressing Run here. Watching a run is a mode of the case
  // screen, not an address - the run has its own once it is worth linking to.
  const [watching, setWatching] = useState<string | null>(null);

  const refreshRuns = useCallback(() => {
    listRuns()
      .then(setRuns)
      .catch((e: Error) => setError(e.message));
  }, []);

  const refreshCases = useCallback(() => {
    listCases()
      .then(setCases)
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    refreshRuns();
    refreshCases();
    getHealth()
      .then((health) => setCanRun(health.can_run))
      .catch(() => setCanRun(false));
  }, [refreshRuns, refreshCases]);

  // Whether the device is busy is not something this tab can know on its own:
  // a run may have been started from the terminal, or from another tab.
  useEffect(() => {
    let live = true;
    const poll = () =>
      getActive()
        .then((body) => live && setActive(body.active))
        .catch(() => undefined);
    poll();
    const timer = setInterval(poll, 4000);
    return () => {
      live = false;
      clearInterval(timer);
    };
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
      navigate({ ...route, runId: detail.id, turn: detail.turns[0].index }, true);
    }
  }, [detail, route, navigate]);

  useEffect(() => {
    if (route.view === "cases") {
      document.title = route.caseId ? `${route.caseId} - Cases` : "Cases";
    } else {
      document.title = route.runId ? `${route.runId} - Runs` : "Runs";
    }
  }, [route]);

  const go = (next: Partial<Route>) => navigate({ ...HOME, ...next });
  const openRun = (runId: string) => {
    setWatching(null);
    go({ view: "runs", runId });
  };

  const turn = detail?.turns.find((t) => t.index === route.turn) ?? null;
  const chosen = cases?.cases.find((c) => c.id === route.caseId) ?? null;
  // Watching can be reached from the busy banner on a case that is not the one
  // running, so the run's own case is looked up rather than assumed.
  const watched =
    (active?.run_id === watching
      ? cases?.cases.find((c) => c.id === active?.case_id)
      : null) ?? chosen;

  const save = async (draft: CaseDraft) => {
    setSaving(true);
    setFormError(null);
    try {
      const saved =
        route.mode === "edit" && route.caseId
          ? await updateCase(route.caseId, draft)
          : await createCase(draft);
      refreshCases();
      go({ view: "cases", caseId: saved.id });
    } catch (e) {
      setFormError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id: string) => {
    try {
      await deleteCase(id);
      refreshCases();
      go({ view: "cases" });
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // Deleted one at a time rather than as a batch, so a run that refuses -
  // because it is still writing - is named, and the rest still go. Returns the
  // refusals for the list to show; an empty array means all of them went.
  const removeRuns = async (ids: string[]): Promise<string[]> => {
    const results = await Promise.all(
      ids.map(async (id) => {
        try {
          await deleteRun(id);
          return { id, error: null as string | null };
        } catch (e) {
          return { id, error: (e as Error).message };
        }
      }),
    );
    refreshRuns();
    // The open run may have just been deleted, which would otherwise leave the
    // middle column showing turns that no longer exist anywhere.
    if (results.some((r) => r.id === route.runId && r.error === null)) {
      go({ view: "runs" });
    }
    return results.flatMap((r) => (r.error === null ? [] : [r.error]));
  };

  const start = async (item: Case) => {
    setStarting(true);
    try {
      const launch = await runCase(item.id);
      setActive(launch);
      setWatching(launch.run_id);
      refreshRuns();
    } catch (e) {
      // 409 is the device being busy, which the banner already explains; it
      // is not an error worth replacing the screen with.
      if (e instanceof ApiError && e.status === 409) {
        getActive().then((body) => setActive(body.active));
      } else {
        setError((e as Error).message);
      }
    } finally {
      setStarting(false);
    }
  };

  if (error !== null) {
    return (
      <Placeholder
        title="The console could not reach its API."
        body={`${error}. Start the server with model-console, then reload.`}
      />
    );
  }

  const showingCases = route.view === "cases";
  const selectedOnRight = showingCases ? route.caseId !== null || route.mode !== null : route.runId !== null;

  return (
    <div
      className={`grid h-screen grid-cols-1 bg-canvas ${
        showingCases
          ? "lg:grid-cols-[264px_minmax(0,1fr)]"
          : "lg:grid-cols-[264px_minmax(0,1fr)_372px]"
      }`}
    >
      {/* Below the breakpoint there is room for one panel, not three. Stacking
          them would bury the chosen run under twenty-two rows of list, so the
          list steps aside once a run is picked and the header offers the way
          back. */}
      <div
        className={`flex min-h-0 flex-col overflow-hidden border-r border-border bg-panel ${
          selectedOnRight ? "hidden lg:flex" : "flex"
        }`}
      >
        <ViewSwitch
          view={route.view}
          counts={{ runs: runs?.length ?? 0, cases: cases?.cases.length ?? 0 }}
          onSelect={(next) => go({ view: next })}
        />
        <div className="min-h-0 flex-1">
          {showingCases ? (
            <CaseList
              cases={cases?.cases ?? []}
              broken={cases?.broken ?? []}
              selected={route.caseId}
              onSelect={(id) => {
                setWatching(null);
                go({ view: "cases", caseId: id });
              }}
              onNew={() => go({ view: "cases", mode: "new" })}
            />
          ) : (
            <RunList
              runs={runs ?? []}
              selected={route.runId}
              filter={filter}
              canDelete={canRun}
              onFilter={setFilter}
              onSelect={(id) => go({ view: "runs", runId: id })}
              onDelete={removeRuns}
            />
          )}
        </div>
      </div>

      <main
        className={`min-w-0 overflow-hidden ${selectedOnRight ? "block" : "hidden lg:block"}`}
      >
        {showingCases ? (
          route.mode === "new" || route.mode === "edit" ? (
            <CaseForm
              key={route.caseId ?? "new"}
              existing={route.mode === "edit" ? chosen : null}
              saving={saving}
              serverError={formError}
              onSave={save}
              onCancel={() =>
                go({ view: "cases", caseId: route.caseId ?? null })
              }
            />
          ) : watching !== null ? (
            <LiveRun
              runId={watching}
              maxActions={watched?.max_actions ?? 20}
              onOpenRun={openRun}
              onBack={() => setWatching(null)}
            />
          ) : chosen ? (
            <CaseDetail
              item={chosen}
              runs={(runs ?? []).filter((r) => r.case_id === chosen.id)}
              canRun={canRun}
              starting={starting}
              busyWith={active?.running ? active.run_id : null}
              onRun={() => start(chosen)}
              onEdit={() => go({ view: "cases", caseId: chosen.id, mode: "edit" })}
              onDelete={() => remove(chosen.id)}
              onOpenRun={(runId) =>
                active?.running && runId === active.run_id
                  ? setWatching(runId)
                  : openRun(runId)
              }
              onBack={() => go({ view: "cases" })}
            />
          ) : route.caseId ? (
            <Placeholder
              title="That case is not here."
              body="It may have been deleted, or the id in the address may be wrong."
            />
          ) : (
            <Placeholder
              title="Pick a case."
              body={
                cases && cases.cases.length === 0
                  ? "There are none yet. A case saves one instruction, the condition that proves it worked, and the budgets that bound the run."
                  : "Every saved case is on the left. Open one to run it or edit it."
              }
            />
          )
        ) : detail ? (
          <RunDetail
            run={detail}
            selectedTurn={route.turn}
            onSelectTurn={(index) =>
              go({ view: "runs", runId: detail.id, turn: index })
            }
            onBack={() => go({ view: "runs" })}
          />
        ) : route.runId ? (
          <Placeholder title="Loading the run." body="Reading its turn records." />
        ) : (
          <Placeholder
            title="Pick a run."
            body={
              runs && runs.length === 0
                ? "There are no runs in this directory yet. Start one from a case, or with model-run."
                : "Every run in the runs directory is on the left, including the ones that did not finish."
            }
          />
        )}
      </main>

      {!showingCases && (
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
      )}
    </div>
  );
}
