import { useEffect, useRef } from "react";
import { CaretLeft } from "@phosphor-icons/react";
import type { Check, Checkpoint, RunDetail as Detail, Turn } from "../api";
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
function OracleCheck({
  check,
  index,
  active,
  onSelect,
}: {
  check: Check;
  index: number;
  active: boolean;
  onSelect: () => void;
}) {
  const verdict =
    check.holds === true ? "pass" : check.holds === false ? "fail" : check.kind;
  const color =
    check.holds === true
      ? "var(--pass)"
      : check.holds === false
        ? "var(--agent)"
        : "var(--infra)";

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={active ? "true" : undefined}
      className={`row relative w-full rounded-md p-3 text-left transition-colors duration-150 ${
        active ? "bg-hover" : "hover:bg-hover"
      }`}
    >
      {active && (
        <span className="absolute inset-y-2 left-0 w-[2px] bg-accent" aria-hidden />
      )}
      <div className="flex items-center gap-2">
        <span className="nums text-[11px] font-semibold" style={{ color }}>
          checker {index + 1}: {verdict}
        </span>
        {check.phase && (
          <span className="nums text-[10px] text-faint">
            {check.phase.replaceAll("_", " ")}
          </span>
        )}
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
    </button>
  );
}

/**
 * The rungs of the case, and which of them the run went through.
 *
 * This is the view that answers "which step broke". A run that ends
 * `checkpoints_incomplete` names the steps in one line of detail; here they
 * are laid out with the turn each was decided at and the check that decided
 * it, so the screen the judge looked at is one click away.
 *
 * Three states, not two. A step that was asked about and not found is an agent
 * that did not pass through it. A step whose trigger never fired is nobody
 * having looked at the right moment - the fix is the pattern, not the agent -
 * and collapsing the two would hide exactly that distinction.
 */
function CheckpointRow({
  rung,
  onSelect,
}: {
  rung: Checkpoint;
  /** Absent when no check on disk decided this rung. */
  onSelect?: () => void;
}) {
  const met = rung.met === true;
  // Only the trigger decides this. A rung nothing ever named still spends a
  // poll on the sweep at the end of the run, so counting polls would report
  // every never-fired rung as "not met" - which blames the agent for a
  // pattern that never matched.
  const looked = rung.triggered === true;
  const verdict = met ? "met" : looked ? "not met" : "never fired";
  const color = met
    ? "var(--pass)"
    : !looked
      ? "var(--infra)"
      : rung.required === false
        ? "var(--faint)"
        : "var(--agent)";

  const body = (
    <>
      <span
        className="step-node mt-[5px] size-[7px] shrink-0 rounded-full"
        style={{
          background: met ? color : "var(--canvas)",
          boxShadow: met ? undefined : `inset 0 0 0 1px ${color}`,
        }}
        aria-hidden
      />
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-baseline gap-x-2">
          <span className="nums text-[12px] font-medium text-text">{rung.id}</span>
          <span className="nums text-[11px] font-semibold" style={{ color }}>
            {verdict}
          </span>
          {rung.required === false && (
            <span className="text-[10.5px] text-faint">observational</span>
          )}
          {rung.turn !== null && rung.turn !== undefined && (
            <span className="nums text-[10.5px] text-faint">turn {rung.turn}</span>
          )}
          {(rung.polls ?? 0) > 1 && (
            <span className="nums text-[10.5px] text-faint">
              {rung.polls} looks
            </span>
          )}
        </span>
        <span className="mt-0.5 block max-w-[72ch] text-[12px] leading-snug text-dim">
          {rung.condition}
        </span>
        {!met && !looked && rung.after && (
          <span className="nums mt-0.5 block text-[11px] text-faint">
            nothing the agent said matched /{rung.after}/
          </span>
        )}
        {rung.trigger_error && (
          <span className="mt-0.5 block text-[11px]" style={{ color: "var(--infra)" }}>
            {rung.trigger_error}
          </span>
        )}
      </span>
    </>
  );

  return onSelect ? (
    <button
      type="button"
      onClick={onSelect}
      className="row flex w-full items-start gap-2.5 rounded-md p-2 text-left transition-colors duration-150 hover:bg-hover"
    >
      {body}
    </button>
  ) : (
    <div className="flex w-full items-start gap-2.5 p-2">{body}</div>
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
        className={`step-node absolute left-[12px] top-[16px] size-[7px] rounded-full ${
          active ? "step-node-active" : "step-node-complete"
        }`}
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
  running,
  selectedTurn,
  selectedCheck,
  onSelectTurn,
  onSelectCheck,
  onBack,
}: {
  run: Detail;
  /** Still writing turns, so its `incomplete` status is not a verdict. */
  running: boolean;
  selectedTurn: number | null;
  selectedCheck: number | null;
  onSelectTurn: (index: number) => void;
  onSelectCheck: (index: number) => void;
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
          <StatusBadge
            outcome={run.outcome}
            status={run.status}
            running={running}
            size="lg"
          />
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

        {run.checkpoints.length > 0 && (
          <div className="space-y-1 p-3">
            <h3 className="text-[10px] uppercase tracking-wide text-faint">
              Named steps
            </h3>
            {run.checkpoints.map((rung) => {
              // The check that decided it: the one that found the step, or
              // failing that the last time anyone looked.
              const asked = run.checks
                .map((check, index) => ({ check, index }))
                .filter(({ check }) => check.checkpoint_id === rung.id);
              const deciding =
                asked.find(({ check }) => check.holds === true) ?? asked.at(-1);
              return (
                <CheckpointRow
                  key={rung.id}
                  rung={rung}
                  onSelect={
                    deciding && onSelectCheck
                      ? () => onSelectCheck(deciding.index)
                      : undefined
                  }
                />
              );
            })}
          </div>
        )}

        {run.checks.length > 0 && (
          <div className="space-y-2 p-3">
            <h3 className="text-[10px] uppercase tracking-wide text-faint">
              Verification
            </h3>
            {run.checks.map((check, index) => (
              <OracleCheck
                key={index}
                check={check}
                index={index}
                active={index === selectedCheck}
                onSelect={() => onSelectCheck(index)}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
