import { existsSync, readdirSync, realpathSync } from "node:fs";
import { isAbsolute, join, relative, resolve } from "node:path";

import { ArtifactStore } from "../knowledge/artifact-store.js";
import { exportStrategyPackage } from "../knowledge/package.js";
import type { SolveSubmitOptions } from "../knowledge/solver.js";
import type { GenerationRow, SQLiteStore } from "../storage/index.js";

export interface KnowledgeApiResponse {
  status: number;
  body: unknown;
}

export interface KnowledgeSolveManager {
  submit(description: string, generations: number, opts?: SolveSubmitOptions): string;
  getStatus(jobId: string): Record<string, unknown>;
  getResult(jobId: string): Record<string, unknown> | null;
}

export interface KnowledgeApiRoutes {
  listSolved(): KnowledgeApiResponse;
  exportScenario(scenarioName: string): KnowledgeApiResponse;
  search(body: Record<string, unknown>): KnowledgeApiResponse;
  submitSolve(body: Record<string, unknown>): KnowledgeApiResponse;
  solveStatus(jobId: string): KnowledgeApiResponse;
}

export function buildKnowledgeApiRoutes(opts: {
  runsRoot: string;
  knowledgeRoot: string;
  openStore: () => SQLiteStore;
  getSolveManager: () => KnowledgeSolveManager;
}): KnowledgeApiRoutes {
  return {
    listSolved: () => ({
      status: 200,
      body: listSolvedScenarios(opts.knowledgeRoot),
    }),
    exportScenario: (scenarioName) => {
      const scenarioDir = resolveKnowledgeScenarioDir(opts.knowledgeRoot, scenarioName);
      if (!scenarioDir) {
        return { status: 422, body: { error: `Invalid scenario '${scenarioName}'` } };
      }
      if (!scenarioHasKnowledge(scenarioDir)) {
        return {
          status: 404,
          body: { error: `No exported knowledge found for scenario '${scenarioName}'` },
        };
      }
      return withStore(opts.openStore, (store) => {
        const artifacts = new ArtifactStore({
          runsRoot: opts.runsRoot,
          knowledgeRoot: opts.knowledgeRoot,
        });
        const pkg = exportStrategyPackage({ scenarioName, artifacts, store });
        return {
          status: 200,
          body: {
            ...pkg,
            suggested_filename: `${scenarioName.replace(/_/g, "-")}-knowledge.md`,
          },
        };
      });
    },
    search: (body) =>
      withStore(opts.openStore, (store) => {
        const query = typeof body.query === "string" ? body.query.trim() : "";
        if (!query) {
          return { status: 422, body: { error: "query is required" } };
        }
        const topK = clampInteger(body.top_k, 5, 1, 20);
        return {
          status: 200,
          body: searchStrategies(store, query, topK),
        };
      }),
    submitSolve: (body) => {
      const description = typeof body.description === "string" ? body.description.trim() : "";
      if (!description) {
        return { status: 422, body: { error: "description is required" } };
      }
      const generations = clampInteger(body.generations, 5, 1, 50);
      const solveOptions = parseSolveSubmitOptions(body);
      if (!solveOptions.ok) {
        return { status: 422, body: { error: solveOptions.error } };
      }
      const jobId = opts.getSolveManager().submit(
        description,
        generations,
        solveOptions.options,
      );
      return { status: 200, body: { job_id: jobId, status: "pending" } };
    },
    solveStatus: (jobId) => {
      const manager = opts.getSolveManager();
      const status = manager.getStatus(jobId);
      if (status.status === "not_found") {
        return { status: 404, body: { detail: status.error ?? `Job '${jobId}' not found` } };
      }
      const result = manager.getResult(jobId);
      return {
        status: 200,
        body: result ? { ...status, result } : status,
      };
    },
  };
}

function listSolvedScenarios(knowledgeRoot: string): Array<{ scenario: string; hasPlaybook: boolean }> {
  const solved: Array<{ scenario: string; hasPlaybook: boolean }> = [];
  if (!existsSync(knowledgeRoot)) {
    return solved;
  }

  for (const name of readdirSync(knowledgeRoot)) {
    if (name.startsWith("_")) {
      continue;
    }
    const scenarioDir = resolveKnowledgeScenarioDir(knowledgeRoot, name);
    if (!scenarioDir) {
      continue;
    }
    const hasPlaybook = existsSync(join(scenarioDir, "playbook.md"));
    if (hasPlaybook) {
      solved.push({ scenario: name, hasPlaybook });
    }
  }
  return solved.sort((a, b) => a.scenario.localeCompare(b.scenario));
}

const KNOWLEDGE_SCENARIO_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;

function resolveKnowledgeScenarioDir(knowledgeRoot: string, scenarioName: string): string | null {
  if (!KNOWLEDGE_SCENARIO_NAME_RE.test(scenarioName)) {
    return null;
  }
  const root = resolve(knowledgeRoot);
  const scenarioDir = resolve(root, scenarioName);
  if (!isChildPath(root, scenarioDir)) {
    return null;
  }
  if (!existsSync(scenarioDir)) {
    return scenarioDir;
  }
  try {
    const realRoot = realpathSync.native(root);
    const realScenarioDir = realpathSync.native(scenarioDir);
    return isChildPath(realRoot, realScenarioDir) ? scenarioDir : null;
  } catch {
    return null;
  }
}

function scenarioHasKnowledge(scenarioDir: string): boolean {
  return existsSync(join(scenarioDir, "playbook.md"))
    || existsSync(join(scenarioDir, "package_metadata.json"));
}

function isChildPath(root: string, candidate: string): boolean {
  const relativePath = relative(root, candidate);
  return relativePath !== "" && !relativePath.startsWith("..") && !isAbsolute(relativePath);
}

function searchStrategies(
  store: Pick<SQLiteStore, "listRuns" | "getGenerations" | "getAgentOutputs">,
  query: string,
  topK: number,
): Array<Record<string, unknown>> {
  const queryLower = query.toLowerCase();
  const results: Array<Record<string, unknown>> = [];
  for (const run of store.listRuns(100)) {
    const generations: GenerationRow[] = store.getGenerations(run.run_id);
    for (const generation of generations) {
      const outputs = store.getAgentOutputs(run.run_id, generation.generation_index);
      const competitor = outputs.find((output) => output.role === "competitor");
      if (!competitor || !competitor.content.toLowerCase().includes(queryLower)) {
        continue;
      }
      results.push({
        scenario: run.scenario,
        display_name: humanizeScenarioName(run.scenario),
        description: "",
        relevance: 1,
        best_score: generation.best_score,
        best_elo: generation.elo,
        match_reason: `Matched generation ${generation.generation_index} competitor output`,
      });
      if (results.length >= topK) {
        return results;
      }
    }
  }
  return results;
}

function withStore(
  openStore: () => SQLiteStore,
  fn: (store: SQLiteStore) => KnowledgeApiResponse,
): KnowledgeApiResponse {
  const store = openStore();
  try {
    return fn(store);
  } finally {
    store.close();
  }
}

function clampInteger(value: unknown, fallback: number, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isInteger(value)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, value));
}

type SolveSubmitOptionsResult =
  | { ok: true; options?: SolveSubmitOptions }
  | { ok: false; error: string };

function parseSolveSubmitOptions(body: Record<string, unknown>): SolveSubmitOptionsResult {
  const family = readOptionalString(body, ["family", "familyOverride", "family_override"]);
  if (!family.ok) {
    return family;
  }
  const budget = readOptionalNonNegativeInteger(body, [
    "generationTimeBudgetSeconds",
    "generationTimeBudget",
    "generation_time_budget_seconds",
    "generation_time_budget",
  ]);
  if (!budget.ok) {
    return budget;
  }

  if (family.value === undefined && budget.value === undefined) {
    return { ok: true };
  }
  return {
    ok: true,
    options: {
      familyOverride: family.value,
      generationTimeBudgetSeconds: budget.value,
    },
  };
}

function readOptionalString(
  body: Record<string, unknown>,
  keys: string[],
): { ok: true; value?: string } | { ok: false; error: string } {
  const entry = firstPresent(body, keys);
  if (!entry) {
    return { ok: true };
  }
  if (typeof entry.value !== "string") {
    return { ok: false, error: `${entry.key} must be a string` };
  }
  const value = entry.value.trim();
  return value ? { ok: true, value } : { ok: true };
}

function readOptionalNonNegativeInteger(
  body: Record<string, unknown>,
  keys: string[],
): { ok: true; value?: number } | { ok: false; error: string } {
  const entry = firstPresent(body, keys);
  if (!entry) {
    return { ok: true };
  }
  if (
    typeof entry.value !== "number"
    || !Number.isInteger(entry.value)
    || entry.value < 0
  ) {
    return { ok: false, error: `${entry.key} must be a non-negative integer` };
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

function humanizeScenarioName(name: string): string {
  return name
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => part[0]!.toUpperCase() + part.slice(1))
    .join(" ");
}
