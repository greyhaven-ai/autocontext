import type { QueueCockpitState } from "../server/cockpit-api.js";
import type { ProgressReportReference } from "../analytics/progress-report.js";
import type { BackgroundSessionSummary } from "../session/background-session-read-model.js";
import type { RuntimeSessionSummary } from "../session/runtime-session-read-model.js";
import { tuiHttpBaseUrl } from "./transport.js";

export type TuiReadFailureKind =
  | "unavailable"
  | "not_found"
  | "unsupported"
  | "server_error";

export type TuiReadResult<T> =
  | { readonly ok: true; readonly value: T }
  | {
      readonly ok: false;
      readonly kind: TuiReadFailureKind;
      readonly status?: number;
      readonly detail: string;
    };

export interface TuiRunSummary {
  readonly run_id: string;
  readonly scenario_name: string;
  readonly status: string;
  readonly generations_completed: number;
  readonly best_score: number;
  readonly best_elo: number;
  readonly created_at: string;
  readonly duration_seconds: number;
  readonly runtime_session?: unknown;
  readonly runtime_session_url?: string;
}

export interface TuiRunStatusReadModel {
  readonly run_id: string;
  readonly scenario_name: string;
  readonly target_generations: number;
  readonly status: string;
  readonly created_at: string;
  readonly generations: readonly TuiRunGenerationReadModel[];
  readonly runtime_session?: RuntimeSessionSummary | null;
  readonly runtime_session_url?: string;
  readonly progress_report?: ProgressReportReference | null;
}

export interface TuiRunGenerationReadModel {
  readonly generation: number;
  readonly mean_score: number;
  readonly best_score: number;
  readonly elo: number;
  readonly wins: number;
  readonly losses: number;
  readonly gate_decision: string;
  readonly status: string;
  readonly duration_seconds: number | null;
  readonly evaluator_epoch: string | null;
  readonly quarantined: number | null;
}

export interface TuiRuntimeSessionListReadModel {
  readonly sessions: readonly RuntimeSessionSummary[];
}

export interface TuiBackgroundSessionListReadModel {
  readonly sessions: readonly BackgroundSessionSummary[];
}

export interface TuiRunInspectionReadModel {
  readonly run: Record<string, unknown>;
  readonly generations: readonly Record<string, unknown>[];
  readonly latest_generation: Record<string, unknown> | null;
  readonly best_generation: Record<string, unknown> | null;
  readonly latest_outputs: readonly { role: string; content: string; generation: number }[];
  readonly best_outputs: readonly { role: string; content: string; generation: number }[];
  readonly runtime_session?: unknown;
  readonly progress_report?: Record<string, unknown> | null;
  readonly artifact_discovery: Readonly<Record<string, string>>;
}

export interface TuiReadModelClientOptions {
  readonly fetchImpl?: typeof fetch;
  readonly watchIntervalMs?: number;
}

export interface TuiWatchOptions {
  readonly signal?: AbortSignal;
  readonly onUpdate?: (status: TuiRunStatusReadModel) => void;
}

export class TuiReadModelClient {
  readonly baseUrl: string;
  readonly #fetch: typeof fetch;
  readonly #watchIntervalMs: number;

  constructor(endpoint: string, options: TuiReadModelClientOptions = {}) {
    this.baseUrl = tuiHttpBaseUrl(endpoint);
    this.#fetch = options.fetchImpl ?? fetch;
    this.#watchIntervalMs = options.watchIntervalMs ?? 2_000;
  }

  listRuns(): Promise<TuiReadResult<readonly TuiRunSummary[]>> {
    return this.#read("/api/cockpit/runs");
  }

  runStatus(runId: string): Promise<TuiReadResult<TuiRunStatusReadModel>> {
    return this.#read(`/api/cockpit/runs/${encodeURIComponent(runId)}/status`);
  }

  runInspection(runId: string): Promise<TuiReadResult<TuiRunInspectionReadModel>> {
    return this.#read(`/api/cockpit/runs/${encodeURIComponent(runId)}/inspection`);
  }

  runTimeline(runId: string): Promise<TuiReadResult<Record<string, unknown>>> {
    return this.#read(
      `/api/cockpit/runs/${encodeURIComponent(runId)}/runtime-session/timeline`,
    );
  }

  runRuntimeSession(runId: string): Promise<TuiReadResult<Record<string, unknown>>> {
    return this.#read(`/api/cockpit/runs/${encodeURIComponent(runId)}/runtime-session`);
  }

  runFindings(runId: string): Promise<TuiReadResult<Record<string, unknown>>> {
    return this.#read(`/api/cockpit/runs/${encodeURIComponent(runId)}/trace-gates`);
  }

  runResumeInfo(runId: string): Promise<TuiReadResult<Record<string, unknown>>> {
    return this.#read(`/api/cockpit/runs/${encodeURIComponent(runId)}/resume`);
  }

  listBackgroundSessions(): Promise<TuiReadResult<TuiBackgroundSessionListReadModel>> {
    return this.#read("/api/cockpit/background-sessions");
  }

  backgroundSession(sessionId: string): Promise<TuiReadResult<Record<string, unknown>>> {
    return this.#read(`/api/cockpit/background-sessions/${encodeURIComponent(sessionId)}`);
  }

  listRuntimeSessions(): Promise<TuiReadResult<TuiRuntimeSessionListReadModel>> {
    return this.#read("/api/cockpit/runtime-sessions");
  }

  runtimeSession(sessionId: string): Promise<TuiReadResult<Record<string, unknown>>> {
    return this.#read(`/api/cockpit/runtime-sessions/${encodeURIComponent(sessionId)}`);
  }

  queueState(): Promise<TuiReadResult<QueueCockpitState>> {
    return this.#read("/api/cockpit/queue");
  }

  approvePlaybook(scenario: string): Promise<TuiReadResult<Record<string, unknown>>> {
    return this.#request(
      `/api/knowledge/${encodeURIComponent(scenario)}/playbook/approve`,
      { method: "POST" },
    );
  }

  rejectPlaybook(scenario: string): Promise<TuiReadResult<Record<string, unknown>>> {
    return this.#request(
      `/api/knowledge/${encodeURIComponent(scenario)}/playbook/reject`,
      { method: "POST" },
    );
  }

  async watchRun(
    runId: string,
    options: TuiWatchOptions = {},
  ): Promise<TuiReadResult<TuiRunStatusReadModel>> {
    let previousFingerprint = "";
    for (;;) {
      if (options.signal?.aborted) {
        return { ok: false, kind: "unavailable", detail: "watch detached" };
      }
      const result = await this.#read<TuiRunStatusReadModel>(
        `/api/cockpit/runs/${encodeURIComponent(runId)}/status`,
        options.signal ? { signal: options.signal } : undefined,
      );
      if (options.signal?.aborted) {
        return { ok: false, kind: "unavailable", detail: "watch detached" };
      }
      if (!result.ok) return result;
      const fingerprint = JSON.stringify(result.value);
      if (fingerprint !== previousFingerprint) {
        previousFingerprint = fingerprint;
        options.onUpdate?.(result.value);
      }
      if (isTerminalStatus(result.value.status)) return result;
      try {
        await abortableDelay(this.#watchIntervalMs, options.signal);
      } catch {
        return { ok: false, kind: "unavailable", detail: "watch detached" };
      }
    }
  }

  async #read<T>(path: string, init?: RequestInit): Promise<TuiReadResult<T>> {
    return this.#request(path, init);
  }

  async #request<T>(path: string, init?: RequestInit): Promise<TuiReadResult<T>> {
    try {
      const response = await this.#fetch(new URL(path, `${this.baseUrl}/`), init);
      const body = await readResponseBody(response);
      if (response.ok) return { ok: true, value: body as T };
      const detail = readDetail(body) ?? `server returned HTTP ${response.status}`;
      if (response.status === 404) {
        return { ok: false, kind: "not_found", status: response.status, detail };
      }
      if (response.status === 405 || response.status === 501) {
        return { ok: false, kind: "unsupported", status: response.status, detail };
      }
      return { ok: false, kind: "server_error", status: response.status, detail };
    } catch (error) {
      return {
        ok: false,
        kind: "unavailable",
        detail: `server unavailable: ${errorMessage(error)}`,
      };
    }
  }
}

export function formatTuiReadFailure(result: Extract<TuiReadResult<unknown>, { ok: false }>): string {
  return `${result.kind.replace("_", " ")}: ${result.detail}`;
}

function isTerminalStatus(status: string): boolean {
  return ["completed", "failed", "stopped", "cancelled", "canceled"].includes(
    status.toLowerCase(),
  );
}

async function readResponseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function readDetail(body: unknown): string | undefined {
  if (typeof body === "string") return body;
  if (!isRecord(body)) return undefined;
  const detail = body.detail ?? body.error;
  return typeof detail === "string" ? detail : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function abortableDelay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason);
      return;
    }
    let timer: ReturnType<typeof setTimeout>;
    const finish = () => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    };
    const onAbort = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      reject(signal?.reason);
    };
    timer = setTimeout(finish, ms);
    timer.unref?.();
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
