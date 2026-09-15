import {
  CheckCircle,
  CircleDashed,
  Wrench,
  XCircle,
  type Icon,
} from "@phosphor-icons/react";
import type { Outcome } from "./api";

// A run either measured the agent or it did not. Reading `oracle_inconclusive`
// as a failed task is what makes a board of results unreadable, so the four
// outcomes never collapse into pass and fail.
export const OUTCOME: Record<
  Outcome,
  { label: string; icon: Icon; color: string }
> = {
  pass: { label: "Verified", icon: CheckCircle, color: "var(--pass)" },
  agent: { label: "Not reached", icon: XCircle, color: "var(--agent)" },
  infrastructure: { label: "Infrastructure", icon: Wrench, color: "var(--infra)" },
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
