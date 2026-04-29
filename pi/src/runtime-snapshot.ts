import { closeSync, existsSync, openSync, readSync, statSync } from "node:fs";
import { isAbsolute, relative, resolve } from "node:path";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type AutoctxModule = any;

export type SettingsLike = {
  dbPath?: string;
  eventStreamPath?: string;
  knowledgeRoot?: string;
  piCommand?: string;
  piModel?: string;
  piRpcApiKey?: string;
  piRpcEndpoint?: string;
  piRpcSessionPersistence?: boolean;
  piTimeout?: number;
  piWorkspace?: string;
  runsRoot?: string;
};

export type StoreLike = {
  listRuns?: (limit?: number, scenario?: string) => Record<string, unknown>[];
  getRun?: (runId: string) => Record<string, unknown> | null;
  getGenerations?: (runId: string) => Record<string, unknown>[];
  getScoreTrajectory?: (runId: string) => Record<string, unknown>[];
  getAgentOutputs?: (runId: string, generationIndex: number) => Record<string, unknown>[];
  getMatchesForRun?: (runId: string) => Record<string, unknown>[];
  listHubPackageRecords?: () => Record<string, unknown>[];
  listHubResultRecords?: () => Record<string, unknown>[];
  listHubPromotionRecords?: () => Record<string, unknown>[];
  close?: () => void;
};

type SessionStoreLike = {
  load?: (sessionId: string) => unknown | null;
  list?: (status?: string, limit?: number) => unknown[];
  close?: () => void;
};

const EVENT_STREAM_TAIL_BYTES = 64 * 1024;
const COMPACTION_LEDGER_TAIL_BYTES = 64 * 1024;

export type RuntimeSnapshotRequest = {
  runId: string;
  sessionId: string;
  scenario: string;
  limit: number;
  includeOutputs: boolean;
  generationIndex: number | null;
};

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function recordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

export function readString(value: Record<string, unknown>, key: string, fallback = ""): string {
  const raw = value[key];
  return typeof raw === "string" ? raw : fallback;
}

function readNumber(value: Record<string, unknown>, key: string): number | null {
  const raw = value[key];
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

function clampLimit(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return 10;
  return Math.min(Math.max(Math.trunc(value), 1), 50);
}

export function resolveSettings(ac: AutoctxModule): SettingsLike {
  try {
    return typeof ac.loadSettings === "function" ? ac.loadSettings() : {};
  } catch {
    return {};
  }
}

function resolveDbPath(settings: SettingsLike): string {
  return process.env.AUTOCONTEXT_DB_PATH ?? settings.dbPath ?? "runs/autocontext.sqlite3";
}

function resolveRunsRoot(settings: SettingsLike): string {
  return process.env.AUTOCONTEXT_RUNS_ROOT ?? settings.runsRoot ?? "runs";
}

function resolveContainedPath(root: string, ...segments: string[]): string | null {
  const resolvedRoot = resolve(root);
  const candidate = resolve(resolvedRoot, ...segments);
  const relativePath = relative(resolvedRoot, candidate);
  if (!relativePath || relativePath.startsWith("..") || isAbsolute(relativePath)) {
    return null;
  }
  return candidate;
}

export function resolveStore(ac: AutoctxModule): StoreLike | null {
  try {
    const settings = resolveSettings(ac);
    return new ac.SQLiteStore(resolveDbPath(settings)) as StoreLike;
  } catch {
    return null;
  }
}

export function runIdOf(run: Record<string, unknown>): string {
  return readString(run, "run_id", readString(run, "id"));
}

function selectedGenerationIndex(
  generations: Record<string, unknown>[],
  requested: number | null,
): number | null {
  if (requested !== null) return requested;
  const last = generations.at(-1);
  return last ? readNumber(last, "generation_index") : null;
}

function compactOutput(output: Record<string, unknown>): Record<string, unknown> {
  const content = readString(output, "content");
  const metadata = { ...output };
  delete metadata.content;
  return {
    ...metadata,
    contentLength: content.length,
    preview: content.slice(0, 500),
  };
}

function safeArrayCall(
  unavailable: string[],
  label: string,
  call: () => Record<string, unknown>[],
): Record<string, unknown>[] {
  try {
    return call();
  } catch (error) {
    unavailable.push(`${label}: ${error instanceof Error ? error.message : String(error)}`);
    return [];
  }
}

function filterByRunOrScenario(
  rows: Record<string, unknown>[],
  request: Pick<RuntimeSnapshotRequest, "runId" | "scenario">,
): Record<string, unknown>[] {
  return rows.filter((row) => {
    const sourceRunId = readString(row, "source_run_id", readString(row, "run_id"));
    const scenarioName = readString(row, "scenario_name", readString(row, "scenario"));
    if (request.runId && sourceRunId) return sourceRunId === request.runId;
    if (request.scenario && scenarioName) return scenarioName === request.scenario;
    return true;
  });
}

function eventMatchesRun(event: Record<string, unknown>, runId: string): boolean {
  if (!runId) return true;
  const payload = isRecord(event.payload) ? event.payload : {};
  return readString(payload, "run_id", readString(payload, "runId", readString(event, "run_id"))) === runId;
}

function readTailText(path: string, maxBytes: number): string {
  const { size } = statSync(path);
  if (size <= 0) return "";
  const bytesToRead = Math.min(size, maxBytes);
  const start = size - bytesToRead;
  const buffer = Buffer.allocUnsafe(bytesToRead);
  const fd = openSync(path, "r");
  try {
    let offset = 0;
    while (offset < bytesToRead) {
      const bytesRead = readSync(fd, buffer, offset, bytesToRead - offset, start + offset);
      if (bytesRead === 0) break;
      offset += bytesRead;
    }
    return buffer.subarray(0, offset).toString("utf-8");
  } finally {
    closeSync(fd);
  }
}

function readRecentEvents(settings: SettingsLike, request: RuntimeSnapshotRequest): Record<string, unknown>[] {
  const eventStreamPath =
    process.env.AUTOCONTEXT_EVENT_STREAM_PATH ??
    settings.eventStreamPath ??
    "runs/events.ndjson";
  if (!existsSync(eventStreamPath)) return [];
  const lines = readTailText(eventStreamPath, EVENT_STREAM_TAIL_BYTES)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(-request.limit * 5);
  const events: Record<string, unknown>[] = [];
  for (const line of lines) {
    try {
      const parsed: unknown = JSON.parse(line);
      if (isRecord(parsed) && eventMatchesRun(parsed, request.runId)) {
        events.push(parsed);
      }
    } catch {
      // Ignore partial event-stream lines.
    }
  }
  return events.slice(-request.limit);
}

function readCompactionLedger(settings: SettingsLike, request: RuntimeSnapshotRequest): Record<string, unknown>[] {
  if (!request.runId) return [];
  const ledgerPath = resolveContainedPath(resolveRunsRoot(settings), request.runId, "compactions.jsonl");
  if (ledgerPath === null) return [];
  if (!existsSync(ledgerPath)) return [];
  const lines = readTailText(ledgerPath, COMPACTION_LEDGER_TAIL_BYTES)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(-request.limit * 5);
  const entries: Record<string, unknown>[] = [];
  for (const line of lines) {
    try {
      const parsed: unknown = JSON.parse(line);
      if (isRecord(parsed) && readString(parsed, "type") === "compaction") {
        entries.push(parsed);
      }
    } catch {
      // Ignore partial ledger lines.
    }
  }
  return entries.slice(-request.limit);
}

function buildSessionSummary(sessionValue: unknown): Record<string, unknown> | null {
  if (!isRecord(sessionValue)) return null;
  const branchPath = typeof sessionValue.branchPath === "function"
    ? sessionValue.branchPath.bind(sessionValue) as (branchId?: string) => unknown[]
    : null;
  const branches = recordArray(sessionValue.branches).map((branch) => {
    const branchId = readString(branch, "branchId");
    const path = branchPath ? recordArray(branchPath(branchId)) : [];
    return {
      branchId,
      label: readString(branch, "label"),
      parentTurnId: readString(branch, "parentTurnId"),
      summary: readString(branch, "summary"),
      pathTurnIds: path.map((turn) => readString(turn, "turnId")).filter(Boolean),
    };
  });
  const turns = recordArray(sessionValue.turns);
  return {
    sessionId: readString(sessionValue, "sessionId"),
    goal: readString(sessionValue, "goal"),
    status: readString(sessionValue, "status"),
    activeBranchId: readString(sessionValue, "activeBranchId"),
    activeTurnId: readString(sessionValue, "activeTurnId"),
    turnCount: readNumber(sessionValue, "turnCount") ?? turns.length,
    totalTokens: readNumber(sessionValue, "totalTokens") ?? 0,
    branches,
    recentEvents: recordArray(sessionValue.events).slice(-5),
  };
}

function resolveSessionStore(ac: AutoctxModule, dbPath: string): SessionStoreLike | null {
  if (typeof ac.SessionStore !== "function") return null;
  try {
    return new ac.SessionStore(dbPath) as SessionStoreLike;
  } catch {
    return null;
  }
}

export function parseRuntimeSnapshotRequest(params: Record<string, unknown>): RuntimeSnapshotRequest {
  const generationIndex = readNumber(params, "generation_index");
  return {
    runId: readString(params, "run_id").trim(),
    sessionId: readString(params, "session_id").trim(),
    scenario: readString(params, "scenario").trim(),
    limit: clampLimit(params.limit),
    includeOutputs: params.include_outputs === true,
    generationIndex,
  };
}

export function collectRuntimeSnapshot(
  ac: AutoctxModule,
  store: StoreLike,
  settings: SettingsLike,
  request: RuntimeSnapshotRequest,
): Record<string, unknown> {
  const unavailable: string[] = [];
  const details: Record<string, unknown> = {
    format: "autocontext.runtime_snapshot.v1",
    unavailable,
  };

  if (request.runId) {
    let run = store.getRun?.(request.runId) ?? null;
    if (!run && store.listRuns) {
      run = store.listRuns(request.limit).find((candidate) => runIdOf(candidate) === request.runId) ?? null;
    }
    if (!run) {
      throw new Error(`Run ${request.runId} not found.`);
    }
    details.run = run;
    const generations = store.getGenerations
      ? safeArrayCall(unavailable, "getGenerations", () => store.getGenerations!(request.runId))
      : [];
    details.generations = generations;
    details.scoreTrajectory = store.getScoreTrajectory
      ? safeArrayCall(unavailable, "getScoreTrajectory", () => store.getScoreTrajectory!(request.runId))
      : [];
    details.matches = store.getMatchesForRun
      ? safeArrayCall(unavailable, "getMatchesForRun", () => store.getMatchesForRun!(request.runId)).slice(0, request.limit)
      : [];
    const generationIndex = selectedGenerationIndex(generations, request.generationIndex);
    if (request.includeOutputs && generationIndex !== null && store.getAgentOutputs) {
      details.agentOutputs = safeArrayCall(
        unavailable,
        "getAgentOutputs",
        () => store.getAgentOutputs!(request.runId, generationIndex),
      ).map(compactOutput);
    }
    details.compactions = readCompactionLedger(settings, request);
  } else if (store.listRuns) {
    details.runs = store.listRuns(request.limit, request.scenario || undefined);
  }

  if (store.listHubPackageRecords) {
    details.packages = filterByRunOrScenario(
      safeArrayCall(unavailable, "listHubPackageRecords", () => store.listHubPackageRecords!()),
      request,
    ).slice(0, request.limit);
  }
  if (store.listHubResultRecords) {
    details.results = filterByRunOrScenario(
      safeArrayCall(unavailable, "listHubResultRecords", () => store.listHubResultRecords!()),
      request,
    ).slice(0, request.limit);
  }
  if (store.listHubPromotionRecords) {
    details.promotions = filterByRunOrScenario(
      safeArrayCall(unavailable, "listHubPromotionRecords", () => store.listHubPromotionRecords!()),
      request,
    ).slice(0, request.limit);
  }

  const sessionStore = resolveSessionStore(ac, resolveDbPath(settings));
  if (sessionStore) {
    try {
      if (request.sessionId) {
        details.session = buildSessionSummary(sessionStore.load?.(request.sessionId) ?? null);
      } else if (typeof sessionStore.list === "function") {
        details.sessions = sessionStore.list(undefined, request.limit)
          .map(buildSessionSummary)
          .filter((session): session is Record<string, unknown> => session !== null);
      }
    } finally {
      sessionStore.close?.();
    }
  } else {
    unavailable.push("SessionStore");
  }

  details.events = readRecentEvents(settings, request);
  return details;
}

function bestScoreFromSnapshot(snapshot: Record<string, unknown>): number | null {
  const generations = recordArray(snapshot.generations);
  const scores = generations
    .map((generation) => readNumber(generation, "best_score"))
    .filter((score): score is number => score !== null);
  return scores.length > 0 ? Math.max(...scores) : null;
}

export function renderRuntimeSnapshot(snapshot: Record<string, unknown>): string {
  const lines = ["Runtime snapshot"];
  const run = isRecord(snapshot.run) ? snapshot.run : null;
  if (run) {
    const bestScore = bestScoreFromSnapshot(snapshot);
    const score = bestScore === null ? "" : ` best=${bestScore.toFixed(3)}`;
    lines.push(`Run ${runIdOf(run)}: ${readString(run, "status", "unknown")}${score}`);
    lines.push(`Generations: ${recordArray(snapshot.generations).length}`);
    lines.push(`Compactions: ${recordArray(snapshot.compactions).length}`);
  } else {
    lines.push(`Runs: ${recordArray(snapshot.runs).length}`);
  }
  lines.push(`Packages: ${recordArray(snapshot.packages).length}`);
  const session = isRecord(snapshot.session) ? snapshot.session : null;
  if (session) {
    lines.push(`Session ${readString(session, "sessionId")}: ${recordArray(session.branches).length} branch(es)`);
  } else {
    lines.push(`Sessions: ${recordArray(snapshot.sessions).length}`);
  }
  lines.push(`Recent events: ${recordArray(snapshot.events).length}`);
  const unavailable = Array.isArray(snapshot.unavailable) ? snapshot.unavailable : [];
  if (unavailable.length > 0) {
    lines.push(`Unavailable: ${unavailable.join(", ")}`);
  }
  return lines.join("\n");
}
