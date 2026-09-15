import { useEffect, useRef } from "react";
import { CaretLeft } from "@phosphor-icons/react";
import type { Check, RunDetail as Detail, Turn } from "../api";
import { clock, duration } from "../format";
import { StatusBadge } from "./StatusBadge";

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-wide text-faint">{label}</dt>
      <dd className="nums mt-0.5 text-[13px] text-text">{value}</dd>
    </div>
  );
}

/** The oracle's two answers, not merely the news that they failed to pair. */
function OracleCheck({ check }: { check: Check }) {
  const verdict =
    check.holds === true ? "pass" : check.holds === false ? "fail" : check.kind;
  const color =
    check.holds === true
      ? "var(--pass)"
      : check.holds === false
        ? "var(--agent)"
        : "var(--infra)";

  return (
    <div className="rounded-md bg-hover p-3">
      <div className="flex items-center gap-2">
        <span className="nums text-[11px] font-semibold" style={{ color }}>
          oracle: {verdict}
        </span>
        {check.attempts > 1 && (
          <span className="nums text-[11px] text-faint">
            {check.attempts} attempts
          </span>
        )}
      </div>
      {check.condition && (
        <p className="nums mt-1.5 text-[12px] text-dim">
          <span className="text-faint">condition</span> {check.condition}
        </p>
      )}
      {check.negation && (
        <p className="nums mt-0.5 text-[12px] text-dim">
          <span className="text-faint">negation</span> {check.negation}
        </p>
      )}
      {check.holds === null && check.detail && (
        <p className="mt-1.5 text-[12px]" style={{ color }}>
          {check.detail}
        </p>
      )}
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
  const row = useRef<HTMLButtonElement>(null);

  // A link to `?turn=11` that lands at turn 0 is not a link to turn 11.
  // Instant rather than smooth: this fires on arrival, where a glide is
  // motion the reader did not ask for.
  useEffect(() => {
    if (active) row.current?.scrollIntoView({ block: "nearest" });
  }, [active]);

  // The line is rendered exactly as the terminal prints it. The trailing
  // verdict is split off only so that `no-change` can be seen at a glance;
  // the characters are unchanged.
  const suffix =
    turn.moved === undefined ? null : turn.moved ? " moved" : " no-change";
  const body = suffix ? turn.line.slice(0, -suffix.length) : turn.line;

  // The rail is drawn a segment at a time, one per row, because the rows are
  // adjacent and the segments therefore join into a single line without
  // anyone having to measure the list. The ends stop at the outermost nodes:
  // the sequence starts at turn 0 and finishes at the last one.
  const rail = first && last ? null : first ? "top-[19px] bottom-0" : last ? "top-0 h-[20px]" : "inset-y-0";

  // A filled node moved the screen; a hollow one did not. The colour repeats
  // what the word at the end of the line already says.
  const node = turn.error
    ? "var(--agent)"
    : active
      ? "var(--accent)"
      : turn.moved === false
        ? "var(--infra)"
        : "var(--faint)";

  return (
    <button
      ref={row}
      onClick={onSelect}
      aria-current={active ? "true" : undefined}
      className={`row relative flex w-full gap-3 py-[11px] pl-7 pr-4 text-left transition-colors duration-150 ${
        active ? "bg-hover" : "hover:bg-hover"
      }`}
    >
      {active && (
        <span className="absolute inset-y-0 left-0 w-[2px] bg-accent" aria-hidden />
      )}
      {rail && (
        <span className={`absolute left-[15px] w-px bg-border ${rail}`} aria-hidden />
      )}
      <span
        className="absolute left-[12px] top-[16px] size-[7px] rounded-full"
        style={{
          background: turn.moved === true ? node : "var(--canvas)",
          boxShadow: turn.moved === true ? undefined : `inset 0 0 0 1px ${node}`,
        }}
        aria-hidden
      />
      <span className="nums w-[22px] shrink-0 text-right text-[15px] leading-[1.3] text-faint">
        {turn.index}
      </span>
      <span className="min-w-0 flex-1">
        {turn.narration && (
          <span className="block max-w-[72ch] text-[13px] leading-snug font-medium text-text">
            {turn.narration}
          </span>
        )}
        {turn.expectation && (
          <span className="mt-0.5 block truncate text-[11.5px] text-dim">
            <span className="text-faint">expect:</span> {turn.expectation}
          </span>
        )}
        <span className="nums mt-1 block text-[11.5px] text-dim">
          <span className="text-faint">-&gt;</span> {body}
          {suffix && (
            <span
              style={{
                color: turn.moved ? "var(--faint)" : "var(--infra)",
                fontWeight: turn.moved ? 400 : 600,
              }}
            >
              {suffix}
            </span>
          )}
        </span>
        {turn.error && (
          <span className="nums mt-1 block text-[11.5px]" style={{ color: "var(--agent)" }}>
            {turn.error}
          </span>
        )}
      </span>
    </button>
  );
}

export function RunDetail({
  run,
  selectedTurn,
  onSelectTurn,
  onBack,
}: {
  run: Detail;
  selectedTurn: number | null;
  onSelectTurn: (index: number) => void;
  onBack: () => void;
}) {
  return (
    <section className="flex h-full min-w-0 flex-col">
      <header className="border-b border-border bg-panel px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={onBack}
            className="-ml-1.5 inline-flex min-h-6 items-center gap-1 rounded-xs px-2 text-[12px] text-dim transition-colors duration-150 hover:text-text lg:hidden"
          >
            <CaretLeft size={13} weight="bold" aria-hidden />
            Runs
          </button>
          <StatusBadge outcome={run.outcome} status={run.status} size="lg" />
          <span className="nums text-[11px] text-faint">
            {clock(run.started_at, run.id)}
          </span>
        </div>

        {/* A line of prose is read, not scanned: at the full width of this
            column the instruction runs to about 130 characters, roughly double
            what an eye tracks comfortably back to the left margin. */}
        <h2 className="mt-2 max-w-[72ch] text-[14px] leading-snug font-medium text-pretty">
          {run.instruction || (
            <span className="text-faint">
              The instruction was never recorded: run.json is written only when
              a run reaches the end.
            </span>
          )}
        </h2>
        {run.success && (
          <p className="mt-1 max-w-[72ch] text-[12px] text-dim text-pretty">
            <span className="text-faint">success:</span> {run.success}
          </p>
        )}
        {run.detail && (
          <p className="mt-1.5 max-w-[72ch] text-[12px] text-dim text-pretty">
            {run.detail}
          </p>
        )}

        <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-2">
          <Metric label="Actions" value={String(run.actions)} />
          <Metric label="Waits" value={String(run.waits)} />
          <Metric label="Turns" value={String(run.turn_count)} />
          <Metric label="Elapsed" value={duration(run.elapsed_s)} />
          {run.warmup_ms !== null && (
            <Metric label="Warm-up" value={`${Math.round(run.warmup_ms)} ms`} />
          )}
          {run.error_class && <Metric label="Class" value={run.error_class} />}
        </dl>
        {run.warmup_error && (
          <p className="nums mt-2 text-[11.5px]" style={{ color: "var(--infra)" }}>
            warm-up: {run.warmup_error}
          </p>
        )}
      </header>

      <div className="scroll flex-1">
        {run.turns.map((turn, index) => (
          <TurnRow
            key={turn.index}
            turn={turn}
            active={turn.index === selectedTurn}
            first={index === 0}
            last={index === run.turns.length - 1}
            onSelect={() => onSelectTurn(turn.index)}
          />
        ))}

        {run.turns.length === 0 && (
          <p className="px-4 py-8 text-center text-[12px] text-faint">
            This run wrote no turns. It failed before the first model call,
            usually a device or configuration error.
          </p>
        )}

        {run.checks.length > 0 && (
          <div className="space-y-2 p-3">
            <h3 className="text-[10px] uppercase tracking-wide text-faint">
              Verification
            </h3>
            {run.checks.map((check, index) => (
              <OracleCheck key={index} check={check} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
