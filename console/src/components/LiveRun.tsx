import { useEffect, useRef, useState } from "react";
import { ArrowLeft } from "@phosphor-icons/react";
import { fileUrl, streamRun, type Outcome, type Turn } from "../api";
import { duration } from "../format";
import { StatusBadge } from "./StatusBadge";

/**
 * How much of a budget a run has spent.
 *
 * The action count is what decides whether a run dies, and until now it was
 * invisible until it was over. A bar is the whole point: the number alone does
 * not say how close to the edge it is.
 */
function Meter({
  label,
  used,
  limit,
}: {
  label: string;
  used: number;
  limit: number;
}) {
  const fraction = limit > 0 ? Math.min(used / limit, 1) : 0;
  // Amber before the run dies, not after. `--infra` is already the colour of
  // "something is wrong but nothing has failed yet".
  const colour = fraction >= 0.9 ? "var(--infra)" : "var(--accent)";
  return (
    <div className="min-w-0 flex-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] text-faint">{label}</span>
        <span className="nums text-[11px] text-dim">
          {used} / {limit}
        </span>
      </div>
      <div
        className="mt-1 h-[3px] w-full overflow-hidden rounded-xs bg-hover"
        role="progressbar"
        aria-valuenow={Math.round(fraction * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
      >
        <div
          className="h-full transition-[width] duration-500"
          style={{ width: `${fraction * 100}%`, background: colour }}
        />
      </div>
    </div>
  );
}

function TurnRow({ runId, turn }: { runId: string; turn: Turn }) {
  return (
    <li className="flex animate-[fade_150ms_ease-out] gap-3 px-5 py-3">
      {turn.marked || turn.screenshot ? (
        <img
          src={fileUrl(runId, (turn.marked ?? turn.screenshot) as string)}
          alt=""
          loading="lazy"
          className="h-16 w-auto shrink-0 rounded-sm border border-border"
        />
      ) : (
        <div className="h-16 w-9 shrink-0 rounded-sm border border-border" aria-hidden />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="nums text-[11px] text-faint">{turn.index}</span>
          <span className="min-w-0 flex-1 text-[12px] leading-relaxed text-text">
            {turn.narration ?? turn.action ?? "-"}
          </span>
        </div>
        {/* Rendered by format.py on the server and sent as a string, so the
            browser and the terminal cannot drift apart. */}
        <p className="nums mt-1 text-[11px] leading-relaxed text-dim">{turn.line}</p>
      </div>
    </li>
  );
}

export function LiveRun({
  runId,
  maxActions,
  onOpenRun,
  onBack,
}: {
  runId: string;
  maxActions: number;
  onOpenRun: (runId: string) => void;
  onBack: () => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [outcome, setOutcome] = useState<Outcome>("incomplete");
  const [status, setStatus] = useState("running");
  const [actions, setActions] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [ended, setEnded] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [budgets, setBudgets] = useState<{ actions: number } | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  // Follow the run, unless someone has scrolled back to read an earlier turn.
  // Yanking them to the bottom every time a turn lands would make the history
  // unreadable for as long as the run is going.
  const following = useRef(true);

  useEffect(() => {
    setTurns([]);
    setEnded(null);
    setActions(0);
    setStartedAt(null);
    following.current = true;
    setStatus("running");
    setOutcome("incomplete");

    return streamRun(runId, {
      onTurn: (turn) =>
        setTurns((current) =>
          // The stream replays from the beginning after a reload, so a turn
          // already held is ignored rather than appended twice.
          current.some((t) => t.index === turn.index) ? current : [...current, turn],
        ),
      onStatus: (next) => {
        setStatus(next.status);
        setOutcome(next.outcome);
        setActions(next.actions);
        if (next.started_at) setStartedAt(Date.parse(next.started_at));
      },
      onEnd: (payload) => {
        if (typeof payload.outcome === "string") setOutcome(payload.outcome as Outcome);
        if (typeof payload.status === "string") setStatus(payload.status);
        setEnded(typeof payload.reason === "string" ? payload.reason : "");
      },
    });
  }, [runId]);

  // The budgets this run is actually measured against. The case passed in is
  // whichever one is selected, which is not necessarily the one running - the
  // busy banner watches a run belonging to another case - and a case can be
  // edited while its run is in flight. The snapshot in the run directory is
  // the case as it was run, so it is the only honest source for a meter.
  useEffect(() => {
    setBudgets(null);
    let live = true;
    fetch(fileUrl(runId, "case.json"))
      .then((response) => (response.ok ? response.json() : null))
      .then((snapshot) => {
        if (live && snapshot) {
          setBudgets({ actions: Number(snapshot.max_actions) });
        }
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [runId]);

  // Elapsed time is not a budget any more - nothing ends a run for taking too
  // long - but a case can now run for as long as it needs, so how long it has
  // been going is the one thing a reader cannot infer from the turns. It counts
  // from when the run started, not from when this page opened, so reattaching
  // to a run already three minutes in shows three minutes.
  useEffect(() => {
    if (ended !== null) return;
    const origin = startedAt ?? Date.now();
    const tick = () => setElapsed((Date.now() - origin) / 1000);
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [runId, ended, startedAt]);

  useEffect(() => {
    if (following.current) {
      bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [turns.length]);

  const live = ended === null;

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border px-5 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <button
              onClick={onBack}
              className="row -ml-1 rounded-xs p-1 text-dim transition-colors duration-150 hover:bg-hover hover:text-text"
              aria-label="Back to the case"
            >
              <ArrowLeft size={15} weight="bold" aria-hidden />
            </button>
            <h2 className="nums truncate text-[13px] font-semibold">{runId}</h2>
            {live && (
              <span className="inline-flex items-center gap-1.5 text-[11px] text-dim">
                <span
                  className="h-1.5 w-1.5 rounded-full bg-accent motion-safe:animate-pulse"
                  aria-hidden
                />
                Running
              </span>
            )}
          </div>
          {!live && <StatusBadge outcome={outcome} status={status} />}
        </div>

        <div className="mt-3 flex items-baseline gap-5">
          <Meter label="Actions" used={actions} limit={budgets?.actions ?? maxActions} />
          <div className="flex shrink-0 items-baseline gap-2">
            <span className="text-[11px] text-faint">Elapsed</span>
            <span className="nums text-[11px] text-dim">{duration(elapsed)}</span>
          </div>
        </div>
      </header>

      <ul
        ref={list}
        onScroll={() => {
          const el = list.current;
          if (el) {
            following.current =
              el.scrollHeight - el.scrollTop - el.clientHeight < 80;
          }
        }}
        className="scroll flex-1"
      >
        {turns.map((turn) => (
          <TurnRow key={turn.index} runId={runId} turn={turn} />
        ))}
        {/* Nothing while the run is still live: the footer already says turns
            appear as they are written, and the header is counting. */}
        {turns.length === 0 && !live && (
          <li className="px-5 py-6 text-[12px] leading-relaxed text-dim">
            This run wrote no turns. Its console.log holds the reason.
          </li>
        )}
        <div ref={bottom} />
      </ul>

      <footer className="border-t border-border px-5 py-3">
        {ended ? (
          <div className="flex items-center justify-between gap-3">
            <span className="text-[11.5px] leading-relaxed text-dim">
              {ended || "The run finished."}
            </span>
            <button
              onClick={() => onOpenRun(runId)}
              className="shrink-0 text-[12px] text-accent"
            >
              Open the full run
            </button>
          </div>
        ) : (
          <p className="text-[11px] leading-relaxed text-dim">
            Turns appear as the run writes them. Closing this page does not stop
            the run.
          </p>
        )}
      </footer>
    </div>
  );
}
