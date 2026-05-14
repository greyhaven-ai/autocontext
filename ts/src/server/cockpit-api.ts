import type { AppSettings } from "../config/index.js";
import { createProvider as defaultCreateProvider } from "../providers/provider-factory.js";
import type { CreateProviderOpts } from "../providers/provider-factory.js";
import { buildContextSelectionReport } from "../knowledge/context-selection-report.js";
import { loadContextSelectionDecisions } from "../knowledge/context-selection-store.js";
import type {
  GenerationRow,
  NotebookRow,
  RunRow,
  SQLiteStore,
} from "../storage/index.js";
import type { RuntimeSessionReadStore } from "../session/runtime-session-read-model.js";
import type { LLMProvider } from "../types/index.js";
import { buildChangelog } from "./cockpit-changelog.js";
import { requestConsultation } from "./cockpit-consultation.js";
import { buildWriteup } from "./cockpit-writeup.js";
import type { NotebookApiRoutes } from "./notebook-api.js";
import {
  runtimeSessionDiscoveryForRun,
} from "./runtime-session-api.js";

export interface CockpitApiResponse {
  status: number;
  body: unknown;
}

export interface CockpitApiRoutes {
  listNotebooks(): CockpitApiResponse;
  getNotebook(sessionId: string): CockpitApiResponse;
  effectiveNotebookContext(sessionId: string): CockpitApiResponse;
  upsertNotebook(sessionId: string, body: Record<string, unknown>): CockpitApiResponse;
  deleteNotebook(sessionId: string): CockpitApiResponse;
  listRuns(): CockpitApiResponse;
  runStatus(runId: string): CockpitApiResponse;
  changelog(runId: string): CockpitApiResponse;
  contextSelection(runId: string): CockpitApiResponse;
  compareGenerations(runId: string, genA: number, genB: number): CockpitApiResponse;
  resumeInfo(runId: string): CockpitApiResponse;
  writeup(runId: string): CockpitApiResponse;
  requestConsultation(runId: string, body: Record<string, unknown>): Promise<CockpitApiResponse>;
  listConsultations(runId: string): CockpitApiResponse;
}

type RoleName = "competitor" | "analyst" | "coach" | "architect";
type NotebookField =
  | "current_objective"
  | "current_hypotheses"
  | "unresolved_questions"
  | "operator_observations"
  | "follow_ups";
type ClosableRuntimeSessionReadStore = RuntimeSessionReadStore & {
  close?: () => void;
};

const ROLE_NOTEBOOK_FIELDS: Record<RoleName, NotebookField[]> = {
  competitor: ["current_objective", "current_hypotheses", "follow_ups"],
  analyst: ["current_objective", "unresolved_questions", "operator_observations"],
  coach: ["current_objective", "follow_ups", "operator_observations"],
  architect: ["current_hypotheses", "unresolved_questions"],
};

const FIELD_HEADERS: Record<NotebookField, string> = {
  current_objective: "Current Objective",
  current_hypotheses: "Active Hypotheses",
  unresolved_questions: "Unresolved Questions",
  operator_observations: "Operator Observations",
  follow_ups: "Follow-ups",
};

export function buildCockpitApiRoutes(opts: {
  openStore: () => SQLiteStore;
  openRuntimeSessionStore?: () => ClosableRuntimeSessionReadStore;
  notebookApi: NotebookApiRoutes;
  settings: AppSettings;
  runsRoot: string;
  knowledgeRoot: string;
  createProvider?: (opts: CreateProviderOpts) => LLMProvider;
}): CockpitApiRoutes {
  const createProvider = opts.createProvider ?? defaultCreateProvider;
  return {
    listNotebooks: () => opts.notebookApi.list(),
    getNotebook: (sessionId) => opts.notebookApi.get(sessionId),
    effectiveNotebookContext: (sessionId) => withStore(opts.openStore, (store) => {
      const notebook = store.getNotebook(sessionId);
      if (!notebook) {
        return { status: 404, body: { detail: `Notebook not found: ${sessionId}` } };
      }
      return {
        status: 200,
        body: buildEffectiveNotebookPreview(notebook, getRunBestScore(store, sessionId)),
      };
    }),
    upsertNotebook: (sessionId, body) => opts.notebookApi.upsert(sessionId, body),
    deleteNotebook: (sessionId) => opts.notebookApi.delete(sessionId),
    listRuns: () => withStore(opts.openStore, (store) => ({
      status: 200,
      body: withRuntimeSessionStore(opts.openRuntimeSessionStore, (runtimeStore) =>
        store.listRuns(50).map((run) => ({
          ...summarizeRun(store, run),
          ...runtimeSessionDiscoveryForRun(runtimeStore, run.run_id),
        }))),
    })),
    runStatus: (runId) => withStore(opts.openStore, (store) => {
      const run = store.getRun(runId);
      if (!run) {
        return { status: 404, body: { detail: `Run '${runId}' not found` } };
      }
      return withRuntimeSessionStore(opts.openRuntimeSessionStore, (runtimeStore) => ({
        status: 200,
        body: {
          run_id: run.run_id,
          scenario_name: run.scenario,
          target_generations: run.target_generations,
          status: run.status,
          created_at: run.created_at,
          generations: store.getGenerations(runId).map(formatGenerationStatus),
          ...runtimeSessionDiscoveryForRun(runtimeStore, runId),
        },
      }));
    }),
    changelog: (runId) => withStore(opts.openStore, (store) => {
      if (!store.getRun(runId)) {
        return { status: 404, body: { detail: `Run '${runId}' not found` } };
      }
      return { status: 200, body: buildChangelog(store, runId) };
    }),
    contextSelection: (runId) => {
      let decisions;
      try {
        decisions = loadContextSelectionDecisions(opts.runsRoot, runId.trim());
      } catch (error) {
        return { status: 422, body: { detail: errorMessage(error) } };
      }
      if (decisions.length === 0) {
        return {
          status: 404,
          body: { detail: `No context selection artifacts found for run '${runId.trim()}'` },
        };
      }
      return { status: 200, body: buildContextSelectionReport(decisions).toDict() };
    },
    compareGenerations: (runId, genA, genB) => withStore(opts.openStore, (store) => {
      const generations = store.getGenerations(runId);
      const rowA = generations.find((generation) => generation.generation_index === genA);
      const rowB = generations.find((generation) => generation.generation_index === genB);
      if (!rowA) {
        return { status: 404, body: { detail: `Generation ${genA} not found for run '${runId}'` } };
      }
      if (!rowB) {
        return { status: 404, body: { detail: `Generation ${genB} not found for run '${runId}'` } };
      }
      return {
        status: 200,
        body: {
          gen_a: formatGenerationComparison(rowA),
          gen_b: formatGenerationComparison(rowB),
          score_delta: roundDelta(rowB.best_score - rowA.best_score),
          elo_delta: roundDelta(rowB.elo - rowA.elo),
        },
      };
    }),
    resumeInfo: (runId) => withStore(opts.openStore, (store) => {
      const run = store.getRun(runId);
      if (!run) {
        return { status: 404, body: { detail: `Run '${runId}' not found` } };
      }
      const generations = store.getGenerations(runId);
      const last = generations.at(-1);
      const lastGeneration = last?.generation_index ?? 0;
      let canResume = run.status === "running" && lastGeneration < run.target_generations;
      let resumeHint: string;
      if (run.status === "completed") {
        resumeHint = "Run completed successfully. Start a new run to continue exploration.";
      } else if (run.status === "running" && lastGeneration >= run.target_generations) {
        resumeHint = "All target generations completed. Mark as complete or increase target.";
        canResume = false;
      } else if (run.status === "running") {
        resumeHint = `Run in progress. Resume from generation ${lastGeneration + 1}.`;
      } else {
        resumeHint = `Run status is '${run.status}'.`;
      }
      const notebook = store.getNotebook(runId);
      return withRuntimeSessionStore(opts.openRuntimeSessionStore, (runtimeStore) => ({
        status: 200,
        body: {
          run_id: runId,
          status: run.status,
          last_generation: lastGeneration,
          last_gate_decision: last?.gate_decision ?? "",
          can_resume: canResume,
          resume_hint: resumeHint,
          effective_notebook_context: notebook
            ? buildEffectiveNotebookPreview(notebook, getRunBestScore(store, runId))
            : null,
          ...runtimeSessionDiscoveryForRun(runtimeStore, runId),
        },
      }));
    }),
    writeup: (runId) => withStore(opts.openStore, (store) => {
      const run = store.getRun(runId);
      if (!run) {
        return { status: 404, body: { detail: `Run '${runId}' not found` } };
      }
      return {
        status: 200,
        body: {
          run_id: run.run_id,
          scenario_name: run.scenario,
          writeup_markdown: buildWriteup(store, run, opts.knowledgeRoot),
        },
      };
    }),
    requestConsultation: (runId, body) => withStoreAsync(opts.openStore, async (store) =>
      requestConsultation(store, {
        body,
        createProvider,
        runId,
        runsRoot: opts.runsRoot,
        settings: opts.settings,
      })),
    listConsultations: (runId) => withStore(opts.openStore, (store) => {
      if (!store.getRun(runId)) {
        return { status: 404, body: { detail: `Run '${runId}' not found` } };
      }
      return { status: 200, body: store.getConsultationsForRun(runId) };
    }),
  };
}

function withStore(
  openStore: () => SQLiteStore,
  fn: (store: SQLiteStore) => CockpitApiResponse,
): CockpitApiResponse {
  const store = openStore();
  try {
    return fn(store);
  } finally {
    store.close();
  }
}

async function withStoreAsync(
  openStore: () => SQLiteStore,
  fn: (store: SQLiteStore) => Promise<CockpitApiResponse>,
): Promise<CockpitApiResponse> {
  const store = openStore();
  try {
    return await fn(store);
  } finally {
    store.close();
  }
}

function withRuntimeSessionStore<T>(
  openStore: (() => ClosableRuntimeSessionReadStore) | undefined,
  fn: (store: RuntimeSessionReadStore | null) => T,
): T {
  if (!openStore) {
    return fn(null);
  }
  const store = openStore();
  try {
    return fn(store);
  } finally {
    store.close?.();
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function summarizeRun(store: SQLiteStore, run: RunRow): Record<string, unknown> {
  const generations = store.getGenerations(run.run_id);
  const bestScore = generations.length > 0
    ? Math.max(...generations.map((generation) => generation.best_score))
    : 0;
  const bestElo = generations.length > 0
    ? Math.max(...generations.map((generation) => generation.elo))
    : 0;
  const totalDuration = generations.reduce(
    (sum, generation) => sum + (generation.duration_seconds ?? 0),
    0,
  );
  return {
    run_id: run.run_id,
    scenario_name: run.scenario,
    generations_completed: generations.length,
    best_score: bestScore,
    best_elo: bestElo,
    status: run.status,
    created_at: run.created_at,
    duration_seconds: Math.round(totalDuration * 10) / 10,
  };
}

function formatGenerationStatus(generation: GenerationRow): Record<string, unknown> {
  return {
    generation: generation.generation_index,
    mean_score: generation.mean_score,
    best_score: generation.best_score,
    elo: generation.elo,
    wins: generation.wins,
    losses: generation.losses,
    gate_decision: generation.gate_decision,
    status: generation.status,
    duration_seconds: generation.duration_seconds,
  };
}

function formatGenerationComparison(generation: GenerationRow): Record<string, unknown> {
  return {
    generation: generation.generation_index,
    mean_score: generation.mean_score,
    best_score: generation.best_score,
    elo: generation.elo,
    gate_decision: generation.gate_decision,
  };
}

function buildEffectiveNotebookPreview(
  notebook: NotebookRow,
  currentBestScore: number | null,
): Record<string, unknown> {
  const roleContexts = Object.fromEntries(
    (Object.keys(ROLE_NOTEBOOK_FIELDS) as RoleName[])
      .map((role) => [role, roleContext(notebook, role)] as const)
      .filter(([, context]) => context.length > 0),
  );
  return {
    session_id: notebook.session_id,
    role_contexts: roleContexts,
    warnings: notebookWarnings(notebook, currentBestScore),
    notebook_empty: isNotebookEmpty(notebook),
    created_at: new Date().toISOString(),
    metadata: {},
  };
}

function roleContext(notebook: NotebookRow, role: RoleName): string {
  const sections = ROLE_NOTEBOOK_FIELDS[role]
    .map((field) => formatNotebookSection(field, notebook[field]))
    .filter((section): section is string => section !== null);
  if (sections.length === 0) {
    return "";
  }
  return `## Session Notebook (${notebook.session_id})\n\n${sections.join("\n\n")}`;
}

function formatNotebookSection(field: NotebookField, value: string | string[]): string | null {
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return null;
    }
    return `### ${FIELD_HEADERS[field]}\n${value.map((item) => `- ${item}`).join("\n")}`;
  }
  if (!value) {
    return null;
  }
  return `### ${FIELD_HEADERS[field]}\n${value}`;
}

function notebookWarnings(
  notebook: NotebookRow,
  currentBestScore: number | null,
): Array<Record<string, string>> {
  if (
    notebook.best_score !== null
    && currentBestScore !== null
    && currentBestScore > notebook.best_score
  ) {
    return [{
      field: "best_score",
      warning_type: "stale_score",
      description: `Notebook best score ${notebook.best_score} is below current run best ${currentBestScore}`,
    }];
  }
  return [];
}

function isNotebookEmpty(notebook: NotebookRow): boolean {
  return !Object.values(ROLE_NOTEBOOK_FIELDS)
    .flat()
    .some((field) => {
      const value = notebook[field];
      return Array.isArray(value) ? value.length > 0 : value.length > 0;
    });
}

function getRunBestScore(store: SQLiteStore, runId: string): number | null {
  const generations = store.getGenerations(runId);
  if (generations.length === 0) {
    return null;
  }
  return Math.max(...generations.map((generation) => generation.best_score));
}

function roundDelta(value: number): number {
  return Math.round(value * 1_000_000) / 1_000_000;
}
