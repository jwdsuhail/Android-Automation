// Mirrors the dataclasses in src/android_runner/console/reader.py.

export type Outcome = "pass" | "agent" | "infrastructure" | "incomplete";

export interface RunSummary {
  id: string;
  started_at: string | null;
  status: string;
  verified: boolean;
  error_class: string | null;
  outcome: Outcome;
  instruction: string;
  success: string | null;
  actions: number;
  waits: number;
  elapsed_s: number | null;
  turn_count: number;
  complete: boolean;
}

export interface Usage {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

export interface Hold {
  ms?: number;
  source?: string;
  requested_ms?: number;
  model_ms?: number;
  notes?: string[];
}

export interface Turn {
  index: number;
  line: string;
  screenshot?: string;
  marked?: string;
  raw?: string;
  narration?: string | null;
  thinking?: string | null;
  check?: string | null;
  expectation?: string | null;
  action?: string;
  arguments?: Record<string, unknown>;
  pixels?: Record<string, unknown>;
  latency_ms?: number;
  usage?: Usage;
  finish_reason?: string;
  reasoning_chars?: number;
  screen_delta?: number;
  screen_tile_max?: number;
  moved?: boolean;
  hold?: Hold;
  error?: string;
  marked_error?: string;
  actor_status?: string;
}

export interface Check {
  holds: boolean | null;
  detail: string;
  kind: string;
  attempts: number;
  errors: string[];
  condition: string | null;
  negation: string | null;
  raw: string;
  negated_raw: string;
}

export interface RunDetail extends RunSummary {
  detail: string;
  turns: Turn[];
  checks: Check[];
  warmup_ms: number | null;
  warmup_error: string | null;
  artifacts: string[];
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `${response.status} from ${path}`);
  }
  return response.json() as Promise<T>;
}

export const listRuns = () => get<RunSummary[]>("/api/runs");
export const getRun = (id: string) => get<RunDetail>(`/api/runs/${id}`);
export const fileUrl = (id: string, name: string) =>
  `/api/runs/${id}/files/${name}`;
