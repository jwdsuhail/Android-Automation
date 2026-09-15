/**
 * Runs and cases are the two halves of the console: what happened, and what to
 * run next. The switch sits above both lists rather than inside either, so
 * neither reads as a filter on the other.
 */
export function ViewSwitch({
  view,
  counts,
  onSelect,
}: {
  view: "runs" | "cases";
  counts: { runs: number; cases: number };
  onSelect: (next: "runs" | "cases") => void;
}) {
  return (
    <div className="flex gap-0.5 border-b border-border px-3 py-2" role="tablist">
      {(["runs", "cases"] as const).map((key) => {
        const active = view === key;
        return (
          <button
            key={key}
            role="tab"
            aria-selected={active}
            onClick={() => onSelect(key)}
            className="inline-flex min-h-6 items-center rounded-xs px-2 text-[11px] capitalize transition-colors duration-150"
            style={{
              background: active ? "var(--hover)" : "transparent",
              color: active ? "var(--text)" : "var(--dim)",
            }}
          >
            {key}
            <span className="nums ml-1 opacity-60">{counts[key]}</span>
          </button>
        );
      })}
    </div>
  );
}
