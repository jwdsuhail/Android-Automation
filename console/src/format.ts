import {
  CheckCircle,
  CircleDashed,
  Scales,
  Wrench,
  XCircle,
  type Icon,
} from "@phosphor-icons/react";
import type { Outcome } from "./api";

// A run either measured the agent or it did not. Reading `oracle_inconclusive`
// as a failed task is what makes a board of results unreadable, so the five
// outcomes never collapse into pass and fail. `oracle` is separate from
// `infrastructure` for the same reason: the harness was fine, the judge was
// not, and only one of those is fixed by restarting a server.
export const OUTCOME: Record<
  Outcome,
  { label: string; icon: Icon; color: string }
> = {
  pass: { label: "Verified", icon: CheckCircle, color: "var(--pass)" },
  agent: { label: "Not reached", icon: XCircle, color: "var(--agent)" },
  infrastructure: { label: "Infrastructure", icon: Wrench, color: "var(--infra)" },
  oracle: { label: "No verdict", icon: Scales, color: "var(--oracle)" },
  incomplete: { label: "Unfinished", icon: CircleDashed, color: "var(--incomplete)" },
};

export function duration(seconds: number | null): string {
  if (seconds === null || Number.isNaN(seconds)) return "-";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds % 60)}s`;
}

export function stamp(iso: string | null, id: string): string {
  if (iso === null) return id;
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function clock(iso: string | null, id: string): string {
  if (iso === null) return id;
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
  });
}
