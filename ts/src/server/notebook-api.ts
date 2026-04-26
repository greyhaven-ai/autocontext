import type { ArtifactStore } from "../knowledge/artifact-store.js";
import type { SQLiteStore, UpsertNotebookOpts } from "../storage/index.js";

export interface NotebookApiResponse {
  status: number;
  body: unknown;
}

export interface NotebookApiRoutes {
  list(): NotebookApiResponse;
  get(sessionId: string): NotebookApiResponse;
  upsert(sessionId: string, body: Record<string, unknown>): NotebookApiResponse;
  delete(sessionId: string): NotebookApiResponse;
}

export function buildNotebookApiRoutes(opts: {
  openStore: () => SQLiteStore;
  artifacts: Pick<ArtifactStore, "writeNotebook" | "deleteNotebook">;
  emitNotebookEvent: (
    event: "notebook_updated" | "notebook_deleted",
    payload: Record<string, unknown>,
  ) => void;
}): NotebookApiRoutes {
  return {
    list: () => withStore(opts.openStore, (store) => ({
      status: 200,
      body: store.listNotebooks(),
    })),
    get: (sessionId) => {
      const invalidSession = validateNotebookSessionId(sessionId);
      if (invalidSession) return invalidSession;
      return withStore(opts.openStore, (store) => {
        const notebook = store.getNotebook(sessionId);
        if (!notebook) {
          return { status: 404, body: { detail: `Notebook not found: ${sessionId}` } };
        }
        return { status: 200, body: notebook };
      });
    },
    upsert: (sessionId, body) => {
      const invalidSession = validateNotebookSessionId(sessionId);
      if (invalidSession) return invalidSession;
      return withStore(opts.openStore, (store) => {
        const request = parseNotebookUpsertRequest(body);
        if (!request.ok) {
          return { status: 422, body: { detail: request.error } };
        }
        const existing = store.getNotebook(sessionId);
        const scenarioName = request.values.scenarioName ?? existing?.scenario_name;
        if (!scenarioName) {
          return {
            status: 400,
            body: { detail: "scenario_name is required when creating a notebook" },
          };
        }
        store.upsertNotebook({
          sessionId,
          ...request.values,
          scenarioName,
        });
        const notebook = store.getNotebook(sessionId);
        if (notebook) {
          opts.artifacts.writeNotebook(sessionId, notebook as unknown as Record<string, unknown>);
          opts.emitNotebookEvent("notebook_updated", {
            session_id: sessionId,
            scenario_name: notebook.scenario_name,
          });
        }
        return { status: 200, body: notebook ?? { session_id: sessionId, scenario_name: scenarioName } };
      });
    },
    delete: (sessionId) => {
      const invalidSession = validateNotebookSessionId(sessionId);
      if (invalidSession) return invalidSession;
      return withStore(opts.openStore, (store) => {
        const existing = store.getNotebook(sessionId);
        const deleted = store.deleteNotebook(sessionId);
        if (!deleted) {
          return { status: 404, body: { detail: `Notebook not found: ${sessionId}` } };
        }
        opts.artifacts.deleteNotebook(sessionId);
        opts.emitNotebookEvent("notebook_deleted", {
          session_id: sessionId,
          scenario_name: existing?.scenario_name ?? "",
        });
        return { status: 200, body: { status: "deleted", session_id: sessionId } };
      });
    },
  };
}

const SAFE_NOTEBOOK_SESSION_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;

function validateNotebookSessionId(sessionId: string): NotebookApiResponse | null {
  if (SAFE_NOTEBOOK_SESSION_ID_RE.test(sessionId)) {
    return null;
  }
  return {
    status: 422,
    body: {
      detail: "Invalid session_id: use letters, digits, underscores, or hyphens; no path separators",
    },
  };
}

function withStore(
  openStore: () => SQLiteStore,
  fn: (store: SQLiteStore) => NotebookApiResponse,
): NotebookApiResponse {
  const store = openStore();
  try {
    return fn(store);
  } finally {
    store.close();
  }
}

type NotebookUpsertRequestResult =
  | { ok: true; values: NotebookUpsertValues }
  | { ok: false; error: string };

type NotebookUpsertValues =
  Partial<Omit<UpsertNotebookOpts, "sessionId" | "scenarioName">>
  & { scenarioName?: string };

function parseNotebookUpsertRequest(body: Record<string, unknown>): NotebookUpsertRequestResult {
  const scenarioName = readOptionalString(body, ["scenario_name", "scenarioName"]);
  if (!scenarioName.ok) return scenarioName;
  const currentObjective = readOptionalString(body, ["current_objective", "currentObjective"]);
  if (!currentObjective.ok) return currentObjective;
  const bestRunId = readOptionalString(body, ["best_run_id", "bestRunId"]);
  if (!bestRunId.ok) return bestRunId;
  const bestGeneration = readOptionalInteger(body, ["best_generation", "bestGeneration"]);
  if (!bestGeneration.ok) return bestGeneration;
  const bestScore = readOptionalNumber(body, ["best_score", "bestScore"]);
  if (!bestScore.ok) return bestScore;
  const currentHypotheses = readOptionalStringArray(body, ["current_hypotheses", "currentHypotheses"]);
  if (!currentHypotheses.ok) return currentHypotheses;
  const unresolvedQuestions = readOptionalStringArray(body, ["unresolved_questions", "unresolvedQuestions"]);
  if (!unresolvedQuestions.ok) return unresolvedQuestions;
  const operatorObservations = readOptionalStringArray(body, ["operator_observations", "operatorObservations"]);
  if (!operatorObservations.ok) return operatorObservations;
  const followUps = readOptionalStringArray(body, ["follow_ups", "followUps"]);
  if (!followUps.ok) return followUps;

  return {
    ok: true,
    values: {
      scenarioName: scenarioName.value,
      currentObjective: currentObjective.value,
      bestRunId: bestRunId.value,
      bestGeneration: bestGeneration.value,
      bestScore: bestScore.value,
      currentHypotheses: currentHypotheses.value,
      unresolvedQuestions: unresolvedQuestions.value,
      operatorObservations: operatorObservations.value,
      followUps: followUps.value,
    },
  };
}

function readOptionalString(
  body: Record<string, unknown>,
  keys: string[],
): { ok: true; value?: string } | { ok: false; error: string } {
  const entry = firstPresent(body, keys);
  if (!entry || entry.value === null) {
    return { ok: true };
  }
  if (typeof entry.value !== "string") {
    return { ok: false, error: `${entry.key} must be a string` };
  }
  return { ok: true, value: entry.value };
}

function readOptionalInteger(
  body: Record<string, unknown>,
  keys: string[],
): { ok: true; value?: number } | { ok: false; error: string } {
  const entry = firstPresent(body, keys);
  if (!entry || entry.value === null) {
    return { ok: true };
  }
  if (typeof entry.value !== "number" || !Number.isInteger(entry.value)) {
    return { ok: false, error: `${entry.key} must be an integer` };
  }
  return { ok: true, value: entry.value };
}

function readOptionalNumber(
  body: Record<string, unknown>,
  keys: string[],
): { ok: true; value?: number } | { ok: false; error: string } {
  const entry = firstPresent(body, keys);
  if (!entry || entry.value === null) {
    return { ok: true };
  }
  if (typeof entry.value !== "number") {
    return { ok: false, error: `${entry.key} must be a number` };
  }
  return { ok: true, value: entry.value };
}

function readOptionalStringArray(
  body: Record<string, unknown>,
  keys: string[],
): { ok: true; value?: string[] } | { ok: false; error: string } {
  const entry = firstPresent(body, keys);
  if (!entry || entry.value === null) {
    return { ok: true };
  }
  if (!Array.isArray(entry.value) || !entry.value.every((value) => typeof value === "string")) {
    return { ok: false, error: `${entry.key} must be an array of strings` };
  }
  return { ok: true, value: entry.value };
}

function firstPresent(
  body: Record<string, unknown>,
  keys: string[],
): { key: string; value: unknown } | null {
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(body, key)) {
      return { key, value: body[key] };
    }
  }
  return null;
}
