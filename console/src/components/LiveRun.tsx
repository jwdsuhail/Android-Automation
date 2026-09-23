import { useEffect, useRef, useState } from "react";
import { ArrowLeft } from "@phosphor-icons/react";
import {
  fileUrl,
  streamRun,
  type Check,
  type Outcome,
  type Turn,
} from "../api";
import { duration } from "../format";
import { CheckDetail } from "./CheckDetail";
import { ScreenViewer } from "./ScreenViewer";
import { StatusBadge } from "./StatusBadge";

type EvidenceSelection = {
  type: "turn" | "check";
  index: number;
};

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

function TurnRow({
  turn,
  active,
  first,
  last,
  onSelect,
}: {
  turn: Turn;
  active: boolean;
  first: boolean;
  last: boolean;
  onSelect: () => void;
}) {
  const rail =
    first && last
      ? null
      : first
        ? "top-[20px] bottom-0"
        : last
          ? "top-0 h-[21px]"
          : "inset-y-0";

  return (
    <li className="step-arrive">
      <button
        type="button"
        onClick={onSelect}
        aria-current={active ? "true" : undefined}
        className={`row relative flex w-full gap-3 py-3 pr-5 pl-8 text-left transition-colors duration-150 ${
          active ? "bg-hover" : "hover:bg-hover"
        }`}
      >
        {active && (
          <span className="absolute inset-y-0 left-0 w-[2px] bg-accent" aria-hidden />
        )}
        {rail && (
          <span className={`absolute left-[16px] w-px bg-border ${rail}`} aria-hidden />
        )}
        <span
          className={`step-node absolute top-[17px] left-[13px] size-[7px] rounded-full ${
            active ? "step-node-active" : "step-node-complete"
          }`}
          aria-hidden
        />
        <span className="nums w-[22px] shrink-0 text-right text-[13px] text-faint">
          {turn.index}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[12.5px] leading-relaxed text-text">
            {turn.narration ?? turn.action ?? "-"}
          </span>
          {/* Rendered by format.py on the server and sent as a string, so the
              browser and the terminal cannot drift apart. */}
          <span className="nums mt-1 block text-[11px] leading-relaxed text-dim">
            {turn.line}
          </span>
        </span>
      </button>
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
  const [checks, setChecks] = useState<Check[]>([]);
  const [selectedTurn, setSelectedTurn] = useState<number | null>(null);
  const [selectedCheck, setSelectedCheck] = useState<number | null>(null);
  const [outcome, setOutcome] = useState<Outcome>("incomplete");
  const [status, setStatus] = useState("running");
  const [actions, setActions] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [ended, setEnded] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [budgets, setBudgets] = useState<{ actions: number } | null>(null);
  const bottom = useRef<HTMLLIElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const turnsSeen = useRef<Turn[]>([]);
  const checksSeen = useRef<Check[]>([]);
  // Following the selected evidence and following the list scroll are separate.
  // Selecting an older step freezes the inspector even if that short list is
  // still physically near its bottom.
  const latestEvidence = useRef<EvidenceSelection | null>(null);
  const followLatest = useRef(true);
  const followScroll = useRef(true);

  useEffect(() => {
    setTurns([]);
    turnsSeen.current = [];
    setChecks([]);
    checksSeen.current = [];
    setSelectedTurn(null);
    setSelectedCheck(null);
    setEnded(null);
    setActions(0);
    setStartedAt(null);
    latestEvidence.current = null;
    followLatest.current = true;
    followScroll.current = true;
    setStatus("running");
    setOutcome("incomplete");

    return streamRun(runId, {
      onTurn: (turn) => {
        // The stream replays from the beginning after a reload, so a turn
        // already held is ignored rather than appended twice.
        if (turnsSeen.current.some((item) => item.index === turn.index)) return;
        const next = [...turnsSeen.current, turn];
        turnsSeen.current = next;
        setTurns(next);
        latestEvidence.current = { type: "turn", index: turn.index };
        if (followLatest.current) {
          setSelectedTurn(turn.index);
          setSelectedCheck(null);
        }
      },
      onCheck: (check) => {
        const duplicate = checksSeen.current.some(
          (item) =>
            item.phase === check.phase &&
            item.turn === check.turn &&
            item.raw === check.raw &&
            item.negated_raw === check.negated_raw,
        );
        if (duplicate) return;
        const next = [...checksSeen.current, check];
        checksSeen.current = next;
        setChecks(next);
        const newestTurn = turnsSeen.current.at(-1);
        // On reconnect the server can replay an entry or earlier actor check
        // after its turns. It is evidence, but it is not the newest evidence.
        const isNewest =
          newestTurn === undefined ||
          (check.phase !== "entry" &&
            (check.turn === null ||
              check.turn === undefined ||
              check.turn >= newestTurn.index));
        if (isNewest) {
          latestEvidence.current = { type: "check", index: next.length - 1 };
        }
        if (isNewest && followLatest.current) {
          setSelectedCheck(next.length - 1);
          setSelectedTurn(null);
        }
      },
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
    if (followScroll.current) {
      bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [turns.length, checks.length]);

  const live = ended === null;
  const chosenTurn =
    turns.find((turn) => turn.index === selectedTurn) ?? turns.at(-1) ?? null;
  const chosenCheck =
    selectedCheck === null ? null : (checks[selectedCheck] ?? null);

  return (
    <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_clamp(360px,36vw,560px)]">
      <div className="flex min-h-0 flex-col">
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
              followScroll.current =
                el.scrollHeight - el.scrollTop - el.clientHeight < 80;
            }
          }}
          className="scroll flex-1"
        >
          {turns.map((turn, index) => (
            <TurnRow
              key={turn.index}
              turn={turn}
              active={selectedCheck === null && turn.index === selectedTurn}
              first={index === 0}
              last={index === turns.length - 1}
              onSelect={() => {
                const latest = latestEvidence.current;
                followLatest.current =
                  latest?.type === "turn" && latest.index === turn.index;
                followScroll.current = followLatest.current;
                setSelectedTurn(turn.index);
                setSelectedCheck(null);
              }}
            />
          ))}
          {checks.map((check, index) => {
            const active = selectedCheck === index;
            const verdict =
              check.holds === true
                ? "pass"
                : check.holds === false
                  ? "fail"
                  : check.kind;
            const color =
              check.holds === true
                ? "var(--pass)"
                : check.holds === false
                  ? "var(--agent)"
                  : "var(--oracle)";
            return (
              <li key={`${check.phase ?? "check"}-${index}`} className="step-arrive">
                <button
                  type="button"
                  onClick={() => {
                    const latest = latestEvidence.current;
                    followLatest.current =
                      latest?.type === "check" && latest.index === index;
                    followScroll.current = followLatest.current;
                    setSelectedCheck(index);
                    setSelectedTurn(null);
                  }}
                  aria-current={active ? "true" : undefined}
                  className={`row relative flex w-full items-center gap-3 py-3 pr-5 pl-8 text-left transition-colors duration-150 ${
                    active ? "bg-hover" : "hover:bg-hover"
                  }`}
                >
                  {active && (
                    <span
                      className="absolute inset-y-0 left-0 w-[2px] bg-accent"
                      aria-hidden
                    />
                  )}
                  <span
                    className={`step-node absolute top-[17px] left-[13px] size-[7px] rounded-full ${
                      active ? "step-node-active" : "step-node-complete"
                    }`}
                    aria-hidden
                  />
                  <span className="nums w-[22px] shrink-0 text-right text-[11px] text-faint">
                    C{index + 1}
                  </span>
                  <span className="min-w-0 flex-1 text-[12px] text-text">
                    Checker{" "}
                    <span className="text-dim">
                      {(check.phase ?? "verification").replaceAll("_", " ")}
                    </span>
                  </span>
                  <span
                    className="nums shrink-0 text-[11px] font-semibold"
                    style={{ color }}
                  >
                    {verdict}
                  </span>
                </button>
              </li>
            );
          })}
        {/* Nothing while the run is still live: the footer already says turns
            appear as they are written, and the header is counting. */}
        {turns.length === 0 && !live && (
          <li className="px-5 py-6 text-[12px] leading-relaxed text-dim">
            This run wrote no turns. Its console.log holds the reason.
          </li>
        )}
          <li ref={bottom} aria-hidden />
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

      <div className="hidden min-h-0 lg:block">
        {chosenCheck && selectedCheck !== null ? (
          <CheckDetail runId={runId} check={chosenCheck} index={selectedCheck} />
        ) : chosenTurn?.screenshot ? (
          <aside className="flex h-full min-h-0 flex-col border-l border-border">
            <header className="shrink-0 border-b border-border px-3 py-2">
              <h3 className="nums text-[12px] font-semibold">
                Turn {chosenTurn.index}
              </h3>
            </header>
            <ScreenViewer
              runId={runId}
              name={chosenTurn.screenshot}
              alt={`Live turn ${chosenTurn.index}`}
              turn={chosenTurn}
              animateAction
            />
          </aside>
        ) : (
          <aside className="grid h-full place-items-center border-l border-border px-6 text-center text-[12px] text-faint">
            The current step has no screen evidence yet.
          </aside>
        )}
      </div>
    </div>
  );
}
