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
  onFilter,
  onSelect,
}: {
  runs: RunSummary[];
  selected: string | null;
  filter: Outcome | "all";
  onFilter: (next: Outcome | "all") => void;
  onSelect: (id: string) => void;
}) {
  const shown = filter === "all" ? runs : runs.filter((r) => r.outcome === filter);

  return (
    <nav className="flex h-full flex-col border-r border-border bg-panel">
      <header className="border-b border-border px-3 py-3">
        <div className="flex items-baseline justify-between">
          <h1 className="text-[13px] font-semibold">Runs</h1>
          <span className="nums text-[11px] text-faint">{runs.length}</span>
        </div>
        <div className="mt-2 flex flex-wrap gap-1">
          {FILTERS.map((key) => {
            const count =
              key === "all"
                ? runs.length
                : runs.filter((r) => r.outcome === key).length;
            const active = filter === key;
            return (
              <button
                key={key}
                onClick={() => onFilter(key)}
                aria-pressed={active}
                className="inline-flex min-h-6 items-center rounded-xs border px-2 text-[11px] transition-colors duration-150"
                style={{
                  borderColor: active ? "var(--border-strong)" : "transparent",
                  background: active ? "var(--raised)" : "transparent",
                  color:
                    key === "all"
                      ? active
                        ? "var(--text)"
                        : "var(--dim)"
                      : OUTCOME[key].color,
                  opacity: count === 0 ? 0.4 : 1,
                }}
              >
                {key === "all" ? "All" : OUTCOME[key].label}
                <span className="nums ml-1 opacity-60">{count}</span>
              </button>
            );
          })}
        </div>
      </header>

      <ul className="scroll flex-1">
        {shown.map((run) => {
          const { icon: Glyph, color } = OUTCOME[run.outcome];
          const active = run.id === selected;
          return (
            <li key={run.id}>
              <button
                onClick={() => onSelect(run.id)}
                aria-current={active ? "true" : undefined}
                className={`relative flex w-full items-start gap-2 px-3 py-[11px] text-left transition-colors duration-150 ${
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
                <Glyph
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
              </button>
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
