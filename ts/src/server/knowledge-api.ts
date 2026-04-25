import { existsSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { ArtifactStore } from "../knowledge/artifact-store.js";
import { exportStrategyPackage } from "../knowledge/package.js";
import type { GenerationRow, SQLiteStore } from "../storage/index.js";

export interface KnowledgeApiResponse {
  status: number;
  body: unknown;
}

export interface KnowledgeSolveManager {
  submit(description: string, generations: number): string;
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
    exportScenario: (scenarioName) =>
      withStore(opts.openStore, (store) => {
        if (!scenarioHasKnowledge(opts.knowledgeRoot, scenarioName)) {
          return {
            status: 404,
            body: { error: `No exported knowledge found for scenario '${scenarioName}'` },
          };
        }
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
      }),
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
      const jobId = opts.getSolveManager().submit(description, generations);
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
    const hasPlaybook = existsSync(join(knowledgeRoot, name, "playbook.md"));
    if (hasPlaybook) {
      solved.push({ scenario: name, hasPlaybook });
    }
  }
  return solved.sort((a, b) => a.scenario.localeCompare(b.scenario));
}

function scenarioHasKnowledge(knowledgeRoot: string, scenarioName: string): boolean {
  return existsSync(join(knowledgeRoot, scenarioName, "playbook.md"))
    || existsSync(join(knowledgeRoot, scenarioName, "package_metadata.json"));
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

function humanizeScenarioName(name: string): string {
  return name
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => part[0]!.toUpperCase() + part.slice(1))
    .join(" ");
}
