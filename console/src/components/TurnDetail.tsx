import { useEffect, useState } from "react";
import { ArrowLeft } from "@phosphor-icons/react";
import type { Turn } from "../api";
import { fileUrl } from "../api";
import { ScreenViewer } from "./ScreenViewer";

type View = "before" | "tap" | "after";

/* No rule under the row: the values start at one x instead, and a column edge
   tracks the eye across a 372px panel as well as a hairline did. */
function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[92px_minmax(0,1fr)] items-baseline gap-3 py-[5px]">
      <span className="text-[11px] text-faint">{label}</span>
      <span className="nums text-[11.5px] text-text">{value}</span>
    </div>
  );
}

export function TurnDetail({
  runId,
  turn,
  artifacts,
  onBack,
}: {
  runId: string;
  turn: Turn;
  artifacts: string[];
  onBack?: () => void;
}) {
  const after = `turn_${String(turn.index).padStart(3, "0")}.after.png`;
  const available: View[] = [];
  if (turn.screenshot) available.push("before");
  if (turn.screenshot || turn.marked) available.push("tap");
  if (artifacts.includes(after)) available.push("after");

  // Tap is the default because it replays the action over the exact screen
  // the model saw. The old marked PNG remains the fallback for legacy turns.
  const preferred: View = available.includes("tap")
    ? "tap"
    : (available[0] ?? "before");
  const [view, setView] = useState<View>(preferred);
  useEffect(() => setView(preferred), [preferred, turn.index]);

  const name =
    view === "tap"
      ? (turn.screenshot ?? turn.marked)
      : view === "after"
        ? after
        : turn.screenshot;
  const usage = turn.usage ?? {};

  return (
    <aside className="flex h-full min-h-0 flex-col border-l border-border bg-panel">
      <header className="z-10 flex shrink-0 items-center justify-between border-b border-border bg-panel px-3 py-2">
        <div className="flex items-center gap-2">
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
          <h3 className="nums text-[12px] font-semibold">Turn {turn.index}</h3>
        </div>
        {available.length > 1 && (
          <div className="flex gap-0.5">
            {available.map((option) => (
              <button
                key={option}
                onClick={() => setView(option)}
                aria-pressed={view === option}
                className="inline-flex min-h-6 items-center rounded-xs px-2 text-[11px] capitalize transition-colors duration-150"
                style={{
                  background: view === option ? "var(--hover)" : "transparent",
                  color: view === option ? "var(--text)" : "var(--dim)",
                }}
              >
                {option}
              </button>
            ))}
          </div>
        )}
      </header>

      {name ? (
        <div className="shrink-0 border-b border-border">
          <ScreenViewer
            runId={runId}
            name={name}
            alt={`Turn ${turn.index}, ${view}`}
            turn={turn}
            animateAction={view === "tap" && name === turn.screenshot}
          />
        </div>
      ) : (
        <p className="px-3 py-6 text-center text-[11.5px] text-faint">
          This action has no place on the screen, so no image was drawn.
        </p>
      )}

      <div className="scroll min-h-0 flex-1 px-3 py-2">
        {turn.check && (
          <p className="mb-2 text-[11.5px] text-dim">
            <span className="text-faint">check:</span> {turn.check}
          </p>
        )}

        <Stat label="action" value={turn.action ?? "-"} />
        {turn.latency_ms !== undefined && (
          <Stat label="latency" value={`${Math.round(turn.latency_ms)} ms`} />
        )}
        {turn.moved !== undefined && (
          <Stat label="screen" value={turn.moved ? "moved" : "no change"} />
        )}
        {turn.screen_delta !== undefined && (
          <Stat label="delta mean" value={turn.screen_delta.toFixed(4)} />
        )}
        {turn.screen_tile_max !== undefined && (
          <Stat label="delta tile max" value={turn.screen_tile_max.toFixed(4)} />
        )}
        {/* "capped" is the one worth seeing: the screen was still moving when
            the wait ran out, so this turn's screenshot may be half drawn. */}
        {turn.settle_ms !== undefined && (
          <Stat
            label="settle"
            value={`${Math.round(turn.settle_ms)} ms${
              turn.settled === false ? " (capped)" : ""
            }`}
          />
        )}
        {usage.prompt_tokens !== undefined && (
          <Stat
            label="tokens"
            value={`${usage.prompt_tokens} in / ${usage.completion_tokens ?? 0} out`}
          />
        )}
        {turn.finish_reason && (
          <Stat label="finish" value={turn.finish_reason} />
        )}
        {turn.reasoning_chars !== undefined && turn.reasoning_chars > 0 && (
          <Stat label="reasoning" value={`${turn.reasoning_chars} chars`} />
        )}
        {turn.actor_status && (
          <Stat label="actor said" value={turn.actor_status} />
        )}

        {turn.hold && (
          <div className="mt-3 rounded-md bg-hover p-3">
            <p className="nums text-[11.5px] text-text">
              held {turn.hold.ms} ms
              {turn.hold.source && turn.hold.source !== "model"
                ? ` from ${turn.hold.source}`
                : ""}
            </p>
            {(turn.hold.notes ?? []).map((note, index) => (
              <p key={index} className="mt-1 text-[11px] text-dim">
                {note}
              </p>
            ))}
          </div>
        )}

        {turn.error && (
          <p
            className="nums mt-3 rounded-md p-3 text-[11.5px]"
            style={{
              color: "var(--agent)",
              background: "color-mix(in srgb, var(--agent) 8%, transparent)",
            }}
          >
            {turn.error}
          </p>
        )}

        <details className="mt-3">
          <summary className="cursor-pointer py-1.5 text-[11px] text-faint">
            Turn record
          </summary>
          <pre className="nums mt-2 overflow-x-auto rounded-sm bg-hover p-2 text-[10.5px] leading-relaxed text-dim">
            {JSON.stringify(turn, null, 2)}
          </pre>
        </details>

        {turn.raw && (
          <p className="mt-2">
            <a
              className="inline-flex min-h-6 items-center text-[11px] text-accent underline-offset-2 hover:underline"
              href={fileUrl(runId, turn.raw)}
              target="_blank"
              rel="noreferrer"
            >
              Open the full reply
            </a>
          </p>
        )}
      </div>
    </aside>
  );
}
