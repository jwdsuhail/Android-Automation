import { Plus } from "@phosphor-icons/react";
import type { Broken, Case } from "../api";

export function CaseList({
  cases,
  broken,
  selected,
  onSelect,
  onNew,
}: {
  cases: Case[];
  broken: Broken[];
  selected: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border px-3 py-2.5">
        <span className="text-[11px] text-faint">
          {cases.length === 0 ? "None saved" : `${cases.length} saved`}
        </span>
        <button
          onClick={onNew}
          className="inline-flex min-h-6 items-center gap-1 rounded-xs px-2 text-[11px] text-dim transition-colors duration-150 hover:bg-hover hover:text-text"
        >
          <Plus size={12} weight="bold" aria-hidden />
          New
        </button>
      </header>

      <ul className="scroll flex-1">
        {cases.map((item) => {
          const active = item.id === selected;
          return (
            <li key={item.id}>
              <button
                onClick={() => onSelect(item.id)}
                aria-current={active ? "true" : undefined}
                className={`row relative flex w-full flex-col items-start px-3 py-[11px] text-left transition-colors duration-150 ${
                  active ? "bg-hover" : "hover:bg-hover"
                }`}
              >
                {active && (
                  <span
                    className="absolute inset-y-0 left-0 w-[2px] bg-accent"
                    aria-hidden
                  />
                )}
                <span className="w-full truncate text-[12px] font-medium text-text">
                  {item.name}
                </span>
                <span className="mt-0.5 w-full truncate text-[11px] text-faint">
                  {item.instruction}
                </span>
              </button>
            </li>
          );
        })}

        {/* A file that will not parse is named rather than skipped. Hiding it
            would leave someone editing a case the console never reads. */}
        {broken.map((bad) => (
          <li
            key={bad.id}
            className="px-3 py-[11px] text-[11px] leading-relaxed text-dim"
          >
            <span className="block text-[12px] text-text">{bad.id}.json</span>
            will not parse: {bad.error}
          </li>
        ))}

        {cases.length === 0 && broken.length === 0 && (
          <li className="px-3 py-6 text-[12px] leading-relaxed text-faint">
            No cases yet. A case is one instruction, the condition that proves it
            worked, and the budgets that bound the run - saved so you can run the
            same test twice.
          </li>
        )}
      </ul>
    </div>
  );
}
