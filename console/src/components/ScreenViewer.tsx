import { useEffect, useState } from "react";
import { ArrowsOutSimple, X } from "@phosphor-icons/react";
import type { Turn } from "../api";
import { fileUrl } from "../api";

type Point = [number, number];

function point(value: unknown): Point | null {
  if (
    !Array.isArray(value) ||
    value.length !== 2 ||
    !value.every((part) => typeof part === "number" && Number.isFinite(part))
  ) {
    return null;
  }
  return [value[0], value[1]];
}

function points(turn: Turn): { start: Point | null; end: Point | null } {
  const values = turn.pixels ?? turn.arguments ?? {};
  const start =
    point(values.coordinate) ??
    point(values.start_coordinate) ??
    point(values.startCoordinate);
  const end =
    point(values.coordinate2) ??
    point(values.end_coordinate) ??
    point(values.endCoordinate);
  return { start, end };
}

function ActionOverlay({
  turn,
  width,
  height,
}: {
  turn: Turn;
  width: number;
  height: number;
}) {
  const { start, end } = points(turn);
  const spatial = start && width > 0 && height > 0;
  const action = turn.action ?? "action";

  if (!spatial) {
    return (
      <span className="absolute top-2 left-2 rounded-xs border border-border-strong bg-black/85 px-2 py-1 nums text-[10px] uppercase tracking-wide text-text">
        {action.replaceAll("_", " ")}
      </span>
    );
  }

  const left = `${(start[0] / width) * 100}%`;
  const top = `${(start[1] / height) * 100}%`;

  if (end) {
    return (
      <svg className="pointer-events-none absolute inset-0 h-full w-full overflow-visible">
        <line
          x1={`${(start[0] / width) * 100}%`}
          y1={`${(start[1] / height) * 100}%`}
          x2={`${(end[0] / width) * 100}%`}
          y2={`${(end[1] / height) * 100}%`}
          className="gesture-path"
          vectorEffect="non-scaling-stroke"
        />
        <circle
          cx={`${(start[0] / width) * 100}%`}
          cy={`${(start[1] / height) * 100}%`}
          r="5"
          className="gesture-origin"
          vectorEffect="non-scaling-stroke"
        />
        <circle
          cx={`${(end[0] / width) * 100}%`}
          cy={`${(end[1] / height) * 100}%`}
          r="5"
          className="gesture-target"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    );
  }

  return (
    <span
      key={`${turn.index}-${action}-${left}-${top}`}
      className={`tap-marker ${action === "long_press" ? "tap-marker-hold" : ""}`}
      style={{ left, top }}
      aria-hidden
    >
      <span />
    </span>
  );
}

export function ScreenViewer({
  runId,
  name,
  alt,
  turn,
  animateAction = false,
}: {
  runId: string;
  name: string;
  alt: string;
  turn?: Turn;
  animateAction?: boolean;
}) {
  const [natural, setNatural] = useState({ width: 0, height: 0 });
  const [expanded, setExpanded] = useState(false);
  const src = fileUrl(runId, name);

  useEffect(() => {
    setNatural({ width: 0, height: 0 });
  }, [src]);

  useEffect(() => {
    if (!expanded) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [expanded]);

  const image = (large: boolean) => (
    <span className={`relative inline-block max-w-full ${large ? "max-h-[94vh]" : ""}`}>
      <img
        src={src}
        alt={alt}
        className={
          large
            ? "max-h-[94vh] max-w-[94vw] object-contain"
            : "h-auto max-h-[68vh] w-auto max-w-full object-contain"
        }
        loading="lazy"
        onLoad={(event) =>
          setNatural({
            width: event.currentTarget.naturalWidth,
            height: event.currentTarget.naturalHeight,
          })
        }
      />
      {animateAction && turn && (
        <ActionOverlay turn={turn} width={natural.width} height={natural.height} />
      )}
    </span>
  );

  return (
    <>
      <div className="group relative flex min-h-0 items-center justify-center overflow-hidden bg-canvas p-3">
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="relative flex max-h-[68vh] w-full items-center justify-center overflow-hidden rounded-sm"
          aria-label="Expand screenshot"
        >
          {image(false)}
          <span className="absolute top-2 right-2 rounded-xs border border-border-strong bg-black/80 p-1.5 text-dim opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100">
            <ArrowsOutSimple size={14} weight="bold" aria-hidden />
          </span>
        </button>
      </div>

      {expanded && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/95 p-3"
          role="dialog"
          aria-modal="true"
          aria-label={alt}
          onClick={() => setExpanded(false)}
        >
          <button
            type="button"
            className="absolute top-4 right-4 z-10 rounded-xs border border-border-strong bg-black/85 p-2 text-text"
            onClick={() => setExpanded(false)}
            aria-label="Close expanded screenshot"
          >
            <X size={16} weight="bold" aria-hidden />
          </button>
          <div onClick={(event) => event.stopPropagation()}>{image(true)}</div>
        </div>
      )}
    </>
  );
}
