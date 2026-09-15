import { useState } from "react";
import { CaretDown, Funnel, Trash } from "@phosphor-icons/react";
import type { Outcome, RunSummary } from "../api";
import { OUTCOME, duration, stamp } from "../format";

const FILTERS: (Outcome | "all")[] = [
  "all",
  "pass",
  "agent",
  "infrastructure",
  "incomplete",
];

export function RunList({
  runs,
  selected,
  filter,
  canDelete,
  onFilter,
  onSelect,
  onDelete,
}: {
  runs: RunSummary[];
  selected: string | null;
  filter: Outcome | "all";
  /* `can_run` from the server. One flag covers both endpoints that write to
     `runs/`, so under `--no-run` there is no Select button at all rather than
     one that leads to a refusal. */
  canDelete: boolean;
  onFilter: (next: Outcome | "all") => void;
  onSelect: (id: string) => void;
  /** Resolves with one message per run that was refused, empty if all went. */
  onDelete: (ids: string[]) => Promise<string[]>;
}) {
  const [picking, setPicking] = useState(false);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  const shown = filter === "all" ? runs : runs.filter((r) => r.outcome === filter);

  // A run can finish, or arrive from the terminal, while this list is open, and
  // the filter can change under a selection. Derived rather than stored so that
  // "Delete 3" can never mean a run that is not on screen: whatever is held for
  // an id that has scrolled out of the filter simply does not count.
  const visible = new Set(shown.map((r) => r.id));
  const marked = [...chosen].filter((id) => visible.has(id));

  const count = (key: Outcome | "all") =>
    key === "all" ? runs.length : runs.filter((r) => r.outcome === key).length;

  const leave = () => {
    setPicking(false);
    setChosen(new Set());
    setConfirming(false);
    setRefused(null);
  };

  const toggle = (id: string) =>
    setChosen((current) => {
      const next = new Set(current);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const remove = async () => {
    setWorking(true);
    const errors = await onDelete(marked);
    setWorking(false);
    setConfirming(false);
    if (errors.length === 0) {
      leave();
      return;
    }
    // Some were kept. Stay in selection mode holding only those, so the
    // message and the rows it is about are on screen together.
    setRefused(errors[0]);
    setChosen(new Set());
  };

  const Glyph = filter === "all" ? Funnel : OUTCOME[filter].icon;

  return (
    <nav className="flex h-full flex-col">
      <header className="border-b border-border px-3 py-2.5">
        {picking ? (
          <div className="flex items-center gap-2">
            <span className="nums flex-1 text-[11.5px] text-dim">
              {marked.length === 0
                ? "Select runs to delete"
                : `${marked.length} of ${shown.length} selected`}
            </span>
            <button
              onClick={() =>
                setChosen(
                  marked.length === shown.length
                    ? new Set()
                    : new Set(shown.map((r) => r.id)),
                )
              }
              className="rounded-xs px-1.5 py-1 text-[11.5px] text-dim transition-colors duration-150 hover:text-text"
            >
              {marked.length === shown.length && shown.length > 0 ? "None" : "All"}
            </button>
            <button
              onClick={leave}
              className="rounded-xs px-1.5 py-1 text-[11.5px] text-dim transition-colors duration-150 hover:text-text"
            >
              Cancel
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <div className="relative min-w-0 flex-1">
              <Glyph
                size={13}
                weight="bold"
                color={filter === "all" ? "var(--faint)" : OUTCOME[filter].color}
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2"
                aria-hidden
              />
              {/* Native, not a built menu. It gets keyboard handling, typeahead
                  and the platform's own popup for free, and this console has
                  no popover primitive to borrow one from. */}
              <select
                value={filter}
                onChange={(e) => onFilter(e.target.value as Outcome | "all")}
                aria-label="Filter runs by outcome"
                className="field field-select"
              >
                {FILTERS.map((key) => (
                  <option key={key} value={key}>
                    {key === "all" ? "All runs" : OUTCOME[key].label} ·{" "}
                    {count(key)}
                  </option>
                ))}
              </select>
              <CaretDown
                size={11}
                weight="bold"
                className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-faint"
                aria-hidden
              />
            </div>
            {canDelete && runs.length > 0 && (
              <button
                onClick={() => setPicking(true)}
                className="shrink-0 rounded-xs px-1.5 py-1 text-[11.5px] text-dim transition-colors duration-150 hover:text-text"
              >
                Select
              </button>
            )}
          </div>
        )}

        {picking && marked.length > 0 && (
          <div className="mt-2">
            {confirming ? (
              // Stacked, not inline. The column is 264px: a sentence and two
              // buttons on one row wraps the text around them, which puts
              // "Delete" in the middle of the words explaining what it does.
              <div>
                <p className="text-[11.5px] leading-relaxed text-text">
                  Delete {marked.length} run{marked.length === 1 ? "" : "s"}?
                  Their screenshots and turn records go with them, and nothing
                  here keeps a copy.
                </p>
                <div className="mt-1.5 flex items-center gap-1">
                  <button
                    onClick={remove}
                    disabled={working}
                    className="inline-flex h-field-sm items-center rounded-xs px-2.5 text-[12px] font-medium transition-colors duration-150 hover:bg-hover disabled:opacity-50"
                    style={{ color: "var(--invalid)" }}
                  >
                    {working ? "Deleting" : "Delete"}
                  </button>
                  <button
                    onClick={() => setConfirming(false)}
                    className="inline-flex h-field-sm items-center rounded-xs px-2.5 text-[12px] text-dim transition-colors duration-150 hover:text-text"
                  >
                    Keep
                  </button>
                </div>
              </div>
            ) : (
              <button
                onClick={() => {
                  setRefused(null);
                  setConfirming(true);
                }}
                className="inline-flex h-field-sm w-full items-center justify-center gap-1.5 rounded-sm border border-border text-[12px] transition-colors duration-150 hover:bg-hover"
                style={{ color: "var(--invalid)" }}
              >
                <Trash size={13} weight="bold" aria-hidden />
                Delete {marked.length} run{marked.length === 1 ? "" : "s"}
              </button>
            )}
          </div>
        )}

        {refused && (
          <p className="mt-2 text-[11.5px] leading-relaxed text-text">{refused}</p>
        )}
      </header>

      <ul className="scroll flex-1">
        {shown.map((run) => {
          const { icon: Mark, color } = OUTCOME[run.outcome];
          const active = run.id === selected;
          const picked = chosen.has(run.id);
          const body = (
            <>
              <Mark
                size={14}
                weight="bold"
                color={color}
                className="mt-[2px] shrink-0"
                aria-hidden
              />
              <span className="min-w-0 flex-1">
                <span className="flex items-baseline justify-between gap-2">
                  <span className="nums text-[11px] text-dim">
                    {stamp(run.started_at, run.id)}
                  </span>
                  <span className="nums shrink-0 text-[11px] text-faint">
                    {run.turn_count}t {duration(run.elapsed_s)}
                  </span>
                </span>
                <span className="mt-0.5 block truncate text-[12px] text-text">
                  {run.instruction || (
                    <em className="not-italic text-faint">
                      instruction not recorded
                    </em>
                  )}
                </span>
              </span>
            </>
          );

          return (
            <li key={run.id}>
              {picking ? (
                <label
                  className={`row flex w-full cursor-pointer items-start gap-2 px-3 py-[11px] transition-colors duration-150 ${
                    picked ? "bg-hover" : "hover:bg-hover"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={picked}
                    onChange={() => toggle(run.id)}
                    className="mt-[3px] h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
                  />
                  {body}
                </label>
              ) : (
                <button
                  onClick={() => onSelect(run.id)}
                  aria-current={active ? "true" : undefined}
                  className={`row relative flex w-full items-start gap-2 px-3 py-[11px] text-left transition-colors duration-150 ${
                    active ? "bg-hover" : "hover:bg-hover"
                  }`}
                >
                  {/* An edge, not a wash: the selected run is marked at the
                      column's own edge, which leaves the status colours as the
                      only saturated thing in the row. */}
                  {active && (
                    <span
                      className="absolute inset-y-0 left-0 w-[2px] bg-accent"
                      aria-hidden
                    />
                  )}
                  {body}
                </button>
              )}
            </li>
          );
        })}
        {shown.length === 0 && (
          <li className="px-3 py-6 text-center text-[12px] text-faint">
            {runs.length === 0
              ? "No runs yet. Start one with model-run."
              : `No ${OUTCOME[filter as Outcome].label.toLowerCase()} runs.`}
          </li>
        )}
      </ul>
    </nav>
  );
}
