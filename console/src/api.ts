// Mirrors the dataclasses in src/android_runner/console/reader.py.

export type Outcome =
  | "pass"
  | "agent"
  | "infrastructure"
  | "oracle"
  | "incomplete";

export interface RunSummary {
  id: string;
  started_at: string | null;
  status: string;
  verified: boolean;
  error_class: string | null;
  outcome: Outcome;
  instruction: string;
  success: string | null;
  case_id: string | null;
  case_name: string | null;
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
  // Absent when MODEL_SETTLE_TIMEOUT_S is 0, so `settled: false` always means
  // the cap ran out rather than that nobody waited.
  settle_ms?: number;
  settled?: boolean;
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

export interface Case {
  id: string;
  name: string;
  instruction: string;
  success: string | null;
  max_actions: number;
  max_waits: number;
  wall_clock_s: number;
  verify_timeout_s: number | null;
  warmup: boolean;
  created_at: string;
  updated_at: string;
}

/** What the form holds before the server has given it an id or timestamps. */
export type CaseDraft = Omit<Case, "id" | "created_at" | "updated_at">;

export interface Broken {
  id: string;
  error: string;
}

export interface CaseListing {
  cases: Case[];
  /* Files that would not parse. Reported, not hidden: a case you cannot see
     is a case you cannot fix. */
  broken: Broken[];
}

export interface Launch {
  run_id: string;
  case_id: string;
  started_at: string;
  pid: number;
  exit_code: number | null;
  running: boolean;
}

export interface Health {
  runs_dir: string;
  cases_dir: string;
  runs: number;
  cases: number;
  built: boolean;
  /* False under `--no-run`, which leaves `runs/` read-only. It gates both
     endpoints that touch it, so this hides the Run button and the run
     delete affordance alike - neither is rendered when it is false. */
  can_run: boolean;
}

/** The status carried alongside the message, so 409 can be told from 422. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function send<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      body.detail ?? `${response.status} from ${path}`,
      response.status,
    );
  }
  return response.status === 204
    ? (undefined as T)
    : (response.json() as Promise<T>);
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const listRuns = () => send<RunSummary[]>("/api/runs");
export const getRun = (id: string) => send<RunDetail>(`/api/runs/${id}`);
export const fileUrl = (id: string, name: string) =>
  `/api/runs/${id}/files/${name}`;
/** Removes the run directory. Nothing keeps a copy; 409 if it is still running. */
export const deleteRun = (id: string) =>
  send<void>(`/api/runs/${id}`, { method: "DELETE" });

export const getHealth = () => send<Health>("/api/health");
export const listCases = () => send<CaseListing>("/api/cases");
export const getCase = (id: string) => send<Case>(`/api/cases/${id}`);
export const createCase = (draft: CaseDraft) =>
  send<Case>("/api/cases", json("POST", draft));
export const updateCase = (id: string, draft: CaseDraft) =>
  send<Case>(`/api/cases/${id}`, json("PUT", draft));
export const deleteCase = (id: string) =>
  send<void>(`/api/cases/${id}`, { method: "DELETE" });
export const runCase = (id: string) =>
  send<Launch>(`/api/cases/${id}/run`, { method: "POST" });
export const getActive = () => send<{ active: Launch | null }>("/api/active");

export interface StreamHandlers {
  onTurn: (turn: Turn) => void;
  onStatus: (status: {
    status: string;
    outcome: Outcome;
    actions: number;
    waits: number;
    started_at: string | null;
  }) => void;
  onEnd: (payload: Record<string, unknown>) => void;
}

/**
 * Turns as the run writes them.
 *
 * The server reads the run directory rather than the runner's output, so this
 * works for a run started at the terminal as well as one started here, and
 * reattaches from the beginning after a reload.
 */
export function streamRun(runId: string, handlers: StreamHandlers): () => void {
  const source = new EventSource(`/api/runs/${runId}/events`);
  source.addEventListener("turn", (e) =>
    handlers.onTurn(JSON.parse((e as MessageEvent).data)),
  );
  source.addEventListener("status", (e) =>
    handlers.onStatus(JSON.parse((e as MessageEvent).data)),
  );
  source.addEventListener("end", (e) => {
    handlers.onEnd(JSON.parse((e as MessageEvent).data));
    // The server closed deliberately. Without this the browser reconnects and
    // replays the whole finished run, forever.
    source.close();
  });
  return () => source.close();
}
