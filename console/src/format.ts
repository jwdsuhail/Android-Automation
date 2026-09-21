import {
  CheckCircle,
  CircleDashed,
  CircleNotch,
  Wrench,
  XCircle,
  type Icon,
} from "@phosphor-icons/react";
import type { Outcome } from "./api";

// What the board is allowed to say. Four outcomes, because the five-value
// vocabulary on the wire is written for whoever is debugging the harness and
// this list is read by whoever wants to know how the runs went: "Not reached"
// and "No verdict" are precise and they stop a new reader dead. `running` is
// the fifth word here but not a fifth outcome - see `Filter`.
//
// Nothing is lost by grouping. `outcome` and `error_class` are untouched on
// disk and on the wire, and a run's own page still prints its exact status
// next to the badge, so the distinction stays one click away.
export type Bucket =
  | "pass"
  | "fail"
  | "infrastructure"
  | "stopped"
  | "running";

// The four the filter offers. `running` is deliberately not among them: it is
// a display state, not an outcome, and a run wearing it is on its way to one
// of the other four. Typed as an exclusion so the filter cannot grow a fifth
// option by accident.
export type Filter = Exclude<Bucket, "running">;

// `oracle` joins infrastructure rather than fail, and that is the one judgement
// in this table. A judge that answered and was unusable says nothing about the
// agent; filing it under Failed would blame the agent for our own parser, and
// on the runs here that would treble the failure count with two thirds of it
// misattributed. Over-reporting our breakage is the safe direction to be wrong
// in. Under-reporting it - by calling it the agent's fault - is not.
const BUCKET: Record<Outcome, Bucket> = {
  pass: "pass",
  agent: "fail",
  infrastructure: "infrastructure",
  oracle: "infrastructure",
  incomplete: "stopped",
};

/**
 * Which word a run wears in the list.
 *
 * `incomplete` means only that there is no `run.json` on disk. That is equally
 * true of a run someone killed and of one that is still writing its turns, and
 * nothing in the run directory tells them apart - the launcher does, through
 * `/api/active`. So the running case is passed in rather than inferred, and
 * without it a run reads as "Manually stopped" for the entire time it runs.
 */
export function bucketOf(outcome: Outcome, running = false): Bucket {
  return running && outcome === "incomplete" ? "running" : BUCKET[outcome];
}

export const OUTCOME: Record<
  Bucket,
  { label: string; icon: Icon; color: string; spin?: boolean }
> = {
  pass: { label: "Passed", icon: CheckCircle, color: "var(--pass)" },
  fail: { label: "Failed", icon: XCircle, color: "var(--agent)" },
  infrastructure: { label: "Infrastructure", icon: Wrench, color: "var(--infra)" },
  stopped: { label: "Manually stopped", icon: CircleDashed, color: "var(--incomplete)" },
  running: {
    label: "Running",
    icon: CircleNotch,
    color: "var(--accent)",
    spin: true,
  },
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
