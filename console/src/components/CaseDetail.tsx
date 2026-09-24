import { useState } from "react";
import { ArrowLeft, PencilSimple, Play, Trash } from "@phosphor-icons/react";
import type { Case, RunSummary } from "../api";
import { duration, stamp } from "../format";
import { StatusBadge } from "./StatusBadge";

function Detail({
  label,
  value,
  numeric = true,
}: {
  label: string;
  value: string;
  numeric?: boolean;
}) {
  return (
    <div className="grid grid-cols-[104px_minmax(0,1fr)] items-baseline gap-3 py-[5px]">
      <span className="text-[11px] text-faint">{label}</span>
      {/* Mono is for figures read down a column. "same as the actor" is a
          sentence, and setting it in mono makes a phrase look like a value. */}
      <span className={`text-[11.5px] text-text ${numeric ? "nums" : ""}`}>
        {value}
      </span>
    </div>
  );
}

export function CaseDetail({
  item,
  runs,
  canRun,
  starting,
  busyWith,
  onRun,
  onEdit,
  onDelete,
  onOpenRun,
  onBack,
}: {
  item: Case;
  runs: RunSummary[];
  canRun: boolean;
  starting: boolean;
  busyWith: string | null;
  onRun: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onOpenRun: (runId: string) => void;
  onBack: () => void;
}) {
  const [confirming, setConfirming] = useState(false);

  return (
    <div className="scroll h-full">
      <header className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-border bg-panel px-5 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <button
            onClick={onBack}
            className="row -ml-1 rounded-xs p-1 text-dim transition-colors duration-150 hover:bg-hover hover:text-text lg:hidden"
            aria-label="Back to cases"
          >
            <ArrowLeft size={15} weight="bold" aria-hidden />
          </button>
          <h2 className="truncate text-[13px] font-semibold">{item.name}</h2>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            onClick={onEdit}
            className="inline-flex h-field-sm items-center gap-1.5 rounded-xs px-2.5 text-[12px] text-dim transition-colors duration-150 hover:bg-hover hover:text-text"
          >
            <PencilSimple size={13} weight="bold" aria-hidden />
            Edit
          </button>
          {canRun && (
            <button
              onClick={onRun}
              disabled={starting || busyWith !== null}
              className="inline-flex h-field-sm items-center gap-1.5 rounded-xs bg-hover px-3 text-[12px] font-medium text-text transition-colors duration-150 hover:bg-border disabled:cursor-not-allowed disabled:text-faint"
            >
              <Play size={13} weight="fill" aria-hidden />
              {starting ? "Starting" : "Run"}
            </button>
          )}
        </div>
      </header>

      <div className="px-5 py-4">
        {/* One emulator cannot run two tests: they would act on each other's
            screen. Say which run is holding it rather than failing on click. */}
        {busyWith !== null && (
          <button
            onClick={() => onOpenRun(busyWith)}
            className="mb-4 w-full rounded-sm bg-hover px-3 py-2 text-left text-[11.5px] leading-relaxed text-dim transition-colors duration-150 hover:text-text"
          >
            A run is already in progress on the device.{" "}
            <span className="text-accent">Watch {busyWith}</span>
          </button>
        )}

        <h3 className="text-[11px] font-medium text-faint">Instruction</h3>
        <p className="mt-1.5 max-w-[68ch] text-[13px] leading-relaxed text-text">
          {item.instruction}
        </p>

        <h3 className="mt-5 text-[11px] font-medium text-faint">Success condition</h3>
        {item.success ? (
          <p className="mt-1.5 max-w-[68ch] text-[13px] leading-relaxed text-text">
            {item.success}
          </p>
        ) : (
          <p className="mt-1.5 max-w-[68ch] text-[12px] leading-relaxed text-dim">
            None. This is an exploration run: the agent can claim it finished,
            but nothing independent can verify it, so it can never be Verified.
          </p>
        )}

        {item.checkpoints?.length > 0 && (
          <>
            <h3 className="mt-5 text-[11px] font-medium text-faint">
              Named steps
            </h3>
            <ul className="mt-1.5 max-w-[68ch] space-y-2">
              {item.checkpoints.map((step) => (
                <li key={step.id}>
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="nums text-[12px] font-medium text-text">
                      {step.id}
                    </span>
                    {!step.required && (
                      <span className="text-[10.5px] text-faint">
                        observational
                      </span>
                    )}
                  </div>
                  <p className="text-[12px] leading-relaxed text-dim">
                    {step.condition}
                  </p>
                  <p className="nums text-[11px] text-faint">
                    {step.after
                      ? `after a turn matching /${step.after}/`
                      : "checked once, at the end of the run"}
                  </p>
                </li>
              ))}
            </ul>
          </>
        )}

        <h3 className="mt-5 text-[11px] font-medium text-faint">Budgets</h3>
        <div className="mt-1">
          <Detail label="Actions" value={String(item.max_actions)} />
          <Detail label="Waits" value={String(item.max_waits)} />
          <Detail
            label="Oracle timeout"
            numeric={item.verify_timeout_s !== null}
            value={
              item.verify_timeout_s === null
                ? "same as the actor"
                : `${item.verify_timeout_s}s`
            }
          />
          <Detail label="Warm-up" numeric={false} value={item.warmup ? "on" : "off"} />
        </div>

        <h3 className="mt-6 flex items-baseline gap-2 text-[11px] font-medium text-faint">
          Runs
          <span className="nums">{runs.length}</span>
        </h3>
        {runs.length === 0 ? (
          <p className="mt-1.5 text-[12px] leading-relaxed text-dim">
            This case has not been run yet.
          </p>
        ) : (
          <ul className="mt-1.5 max-w-[68ch]">
            {runs.map((run) => (
              <li key={run.id}>
                <button
                  onClick={() => onOpenRun(run.id)}
                  className="row flex w-full items-center justify-between gap-3 rounded-sm px-2 py-2 text-left transition-colors duration-150 hover:bg-hover"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <StatusBadge
                      outcome={run.outcome}
                      running={run.id === busyWith}
                    />
                    <span className="nums truncate text-[11px] text-dim">
                      {stamp(run.started_at, run.id)}
                    </span>
                  </span>
                  <span className="nums shrink-0 text-[11px] text-faint">
                    {run.turn_count}t {duration(run.elapsed_s)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}

        <div className="mt-8 border-t border-border pt-4">
          {confirming ? (
            <div className="flex items-center gap-2">
              <span className="text-[12px] text-text">
                Delete this case? Its runs are kept.
              </span>
              <button
                onClick={onDelete}
                className="inline-flex h-field-sm items-center rounded-xs px-2.5 text-[12px] font-medium"
                style={{ color: "var(--invalid)" }}
              >
                Delete
              </button>
              <button
                onClick={() => setConfirming(false)}
                className="inline-flex h-field-sm items-center rounded-xs px-2.5 text-[12px] text-dim hover:text-text"
              >
                Keep
              </button>
            </div>
          ) : (
            <button
              onClick={() => setConfirming(true)}
              className="inline-flex h-field-sm items-center gap-1.5 rounded-xs px-2.5 text-[12px] text-faint transition-colors duration-150 hover:bg-hover hover:text-text"
            >
              <Trash size={13} weight="bold" aria-hidden />
              Delete case
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
