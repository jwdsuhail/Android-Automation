import { ArrowLeft } from "@phosphor-icons/react";
import type { Check } from "../api";
import { ScreenViewer } from "./ScreenViewer";

function Answer({
  label,
  value,
  raw,
}: {
  label: string;
  value: string | null;
  raw: string;
}) {
  const color =
    value === "success"
      ? "var(--pass)"
      : value === "fail"
        ? "var(--agent)"
        : "var(--faint)";

  return (
    <div className="rounded-md bg-hover p-3">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[11px] text-faint">{label}</span>
        <span className="nums text-[11px] font-semibold" style={{ color }}>
          {value ?? "no answer"}
        </span>
      </div>
      {raw && (
        <details className="mt-2">
          <summary className="cursor-pointer py-1 text-[11px] text-dim">
            Checker reply
          </summary>
          <pre className="nums mt-1 overflow-x-auto whitespace-pre-wrap rounded-sm bg-canvas p-2 text-[10.5px] leading-relaxed text-dim">
            {raw}
          </pre>
        </details>
      )}
    </div>
  );
}

export function CheckDetail({
  runId,
  check,
  index,
  onBack,
}: {
  runId: string;
  check: Check;
  index: number;
  onBack?: () => void;
}) {
  const verdict =
    check.holds === true ? "pass" : check.holds === false ? "fail" : check.kind;
  const color =
    check.holds === true
      ? "var(--pass)"
      : check.holds === false
        ? "var(--agent)"
        : "var(--oracle)";

  return (
    <aside className="flex h-full min-h-0 flex-col border-l border-border bg-panel">
      <header className="flex shrink-0 items-center justify-between border-b border-border px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          {onBack && (
            <button
              type="button"
              onClick={onBack}
              className="rounded-xs p-1 text-dim transition-colors duration-150 hover:bg-hover hover:text-text lg:hidden"
              aria-label="Back to steps"
            >
              <ArrowLeft size={15} weight="bold" aria-hidden />
            </button>
          )}
          <h3 className="truncate text-[12px] font-semibold">
            Checker {index + 1}
          </h3>
        </div>
        <span className="nums text-[11px] font-semibold" style={{ color }}>
          {verdict}
        </span>
      </header>

      {check.screenshot ? (
        <div className="shrink-0 border-b border-border">
          <ScreenViewer
            runId={runId}
            name={check.screenshot}
            alt={`Checker ${index + 1} saw this screen`}
          />
        </div>
      ) : (
        <div className="border-b border-border px-4 py-8 text-center text-[11.5px] text-faint">
          This older check did not record which screen it judged.
        </div>
      )}

      <div className="scroll min-h-0 flex-1 space-y-2 px-3 py-3">
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-faint">
          <span>
            phase <span className="nums text-dim">{check.phase ?? "unknown"}</span>
          </span>
          <span>
            attempts <span className="nums text-dim">{check.attempts}</span>
          </span>
          {check.turn !== null && check.turn !== undefined && (
            <span>
              turn <span className="nums text-dim">{check.turn}</span>
            </span>
          )}
        </div>

        {check.detail && (
          <p className="text-[12px] leading-relaxed text-dim">{check.detail}</p>
        )}

        <Answer label="Success condition" value={check.condition} raw={check.raw} />
        <Answer
          label="Negated condition"
          value={check.negation}
          raw={check.negated_raw}
        />

        {check.errors.length > 0 && (
          <div className="rounded-md bg-hover p-3">
            <p className="text-[11px] text-faint">Transport errors</p>
            {check.errors.map((error, errorIndex) => (
              <p key={errorIndex} className="nums mt-1 text-[10.5px] text-dim">
                {error}
              </p>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}
