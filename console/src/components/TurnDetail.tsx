import { useEffect, useState } from "react";
import type { Turn } from "../api";
import { fileUrl } from "../api";

type View = "before" | "marked" | "after";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2 border-b border-border py-1">
      <span className="text-[11px] text-faint">{label}</span>
      <span className="nums text-[11.5px] text-text">{value}</span>
    </div>
  );
}

export function TurnDetail({
  runId,
  turn,
  artifacts,
}: {
  runId: string;
  turn: Turn;
  artifacts: string[];
}) {
  const after = `turn_${String(turn.index).padStart(3, "0")}.after.png`;
  const available: View[] = [];
  if (turn.screenshot) available.push("before");
  if (turn.marked) available.push("marked");
  if (artifacts.includes(after)) available.push("after");

  // The marked copy is the default because it is the only image that shows
  // what the model aimed at, rather than only where that left the app.
  const preferred: View = available.includes("marked")
    ? "marked"
    : (available[0] ?? "before");
  const [view, setView] = useState<View>(preferred);
  useEffect(() => setView(preferred), [preferred, turn.index]);

  const name =
    view === "marked" ? turn.marked : view === "after" ? after : turn.screenshot;
  const usage = turn.usage ?? {};

  return (
    <aside className="scroll h-full border-l border-border bg-panel">
      <header className="flex items-center justify-between border-b border-border px-3 py-2">
        <h3 className="nums text-[12px] font-semibold">Turn {turn.index}</h3>
        {available.length > 1 && (
          <div className="flex gap-0.5 rounded-sm border border-border p-0.5">
            {available.map((option) => (
              <button
                key={option}
                onClick={() => setView(option)}
                aria-pressed={view === option}
                className="inline-flex min-h-6 items-center rounded-xs px-2 text-[11px] capitalize transition-colors duration-150"
                style={{
                  background: view === option ? "var(--raised)" : "transparent",
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
        <a
          href={fileUrl(runId, name)}
          target="_blank"
          rel="noreferrer"
          className="block bg-raised p-2"
        >
          <img
            src={fileUrl(runId, name)}
            alt={`Turn ${turn.index}, ${view}`}
            className="mx-auto max-h-[46vh] w-auto rounded-sm border border-border"
            loading="lazy"
          />
        </a>
      ) : (
        <p className="px-3 py-6 text-center text-[11.5px] text-faint">
          This action has no place on the screen, so no image was drawn.
        </p>
      )}

      <div className="px-3 py-2">
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
          <div className="mt-3 rounded-md border border-border bg-raised p-3">
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
            className="nums mt-3 rounded-md border p-3 text-[11.5px]"
            style={{
              color: "var(--agent)",
              borderColor: "color-mix(in srgb, var(--agent) 35%, transparent)",
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
          <pre className="nums mt-2 overflow-x-auto rounded-sm border border-border bg-raised p-2 text-[10.5px] leading-relaxed text-dim">
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
