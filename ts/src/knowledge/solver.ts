/**
 * Solve-on-demand manager — submit, track, and retrieve solve jobs (AC-370).
 * Mirrors Python's autocontext/knowledge/solver.py.
 */

import type { LLMProvider } from "../types/index.js";
import type { SQLiteStore } from "../storage/index.js";
import {
  buildSolveJobId,
  createSolveExecutionDeps,
  executeSolveJobWorkflow,
  runAgentTaskSolveJob,
  runBuiltInGameSolveJob,
  runCodegenSolveJob,
} from "./solve-manager-workflow.js";
import {
  createSolveJob,
  getCompletedSolveJobResult,
  getSolveJobStatus,
  type SolveJob,
} from "./solve-workflow.js";

export interface SolveManagerOpts {
  provider: LLMProvider;
  store: SQLiteStore;
  runsRoot: string;
  knowledgeRoot: string;
}

export interface SolveSubmitOptions {
  familyOverride?: string;
  generationTimeBudgetSeconds?: number | null;
}

export { buildAgentTaskSolveSpec } from "./agent-task-solve-execution.js";

export class SolveManager {
  #provider: LLMProvider;
  #store: SQLiteStore;
  #runsRoot: string;
  #knowledgeRoot: string;
  #jobs = new Map<string, SolveJob>();

  constructor(opts: SolveManagerOpts) {
    this.#provider = opts.provider;
    this.#store = opts.store;
    this.#runsRoot = opts.runsRoot;
    this.#knowledgeRoot = opts.knowledgeRoot;
  }

  submit(description: string, generations: number, opts: SolveSubmitOptions = {}): string {
    const jobId = buildSolveJobId();
    const job = createSolveJob(jobId, description, generations, opts);
    this.#jobs.set(jobId, job);

    this.#runJob(job).catch(() => {
      // executeSolveJobWorkflow normalizes failures onto the job record.
    });

    return jobId;
  }

  getStatus(jobId: string): Record<string, unknown> {
    return getSolveJobStatus(jobId, this.#jobs.get(jobId));
  }

  getResult(jobId: string): Record<string, unknown> | null {
    return getCompletedSolveJobResult(this.#jobs.get(jobId));
  }

  async #runJob(job: SolveJob): Promise<void> {
    await executeSolveJobWorkflow({
      job,
      provider: this.#provider,
      store: this.#store,
      runsRoot: this.#runsRoot,
      knowledgeRoot: this.#knowledgeRoot,
      deps: createSolveExecutionDeps({
        provider: this.#provider,
        store: this.#store,
        runsRoot: this.#runsRoot,
        knowledgeRoot: this.#knowledgeRoot,
      }),
    });
  }

  async runGameScenario(job: SolveJob, scenarioName: string): Promise<void> {
    await runBuiltInGameSolveJob({
      job,
      provider: this.#provider,
      store: this.#store,
      runsRoot: this.#runsRoot,
      knowledgeRoot: this.#knowledgeRoot,
      scenarioName,
      generations: job.generations,
      executeBuiltInGameSolve: createSolveExecutionDeps({
        provider: this.#provider,
        store: this.#store,
        runsRoot: this.#runsRoot,
        knowledgeRoot: this.#knowledgeRoot,
      }).executeBuiltInGameSolve,
    });
  }

  async runAgentTaskScenario(
    job: SolveJob,
    created: { name: string; spec: Record<string, unknown> },
  ): Promise<void> {
    await runAgentTaskSolveJob({
      job,
      provider: this.#provider,
      created,
      generations: job.generations,
      executeAgentTaskSolve: createSolveExecutionDeps({
        provider: this.#provider,
        store: this.#store,
        runsRoot: this.#runsRoot,
        knowledgeRoot: this.#knowledgeRoot,
      }).executeAgentTaskSolve,
    });
  }

  async runCodegenScenario(
    job: SolveJob,
    created: { name: string; family: string; spec: Record<string, unknown> },
    family: import("../scenarios/families.js").ScenarioFamilyName,
  ): Promise<void> {
    await runCodegenSolveJob({
      job,
      knowledgeRoot: this.#knowledgeRoot,
      created,
      family,
      executeCodegenSolve: createSolveExecutionDeps({
        provider: this.#provider,
        store: this.#store,
        runsRoot: this.#runsRoot,
        knowledgeRoot: this.#knowledgeRoot,
      }).executeCodegenSolve,
    });
  }
}
