import Database from "better-sqlite3";

import type {
  AgentOutputRow,
  GenerationRow,
  HumanFeedbackRow,
  InsertMonitorAlertOpts,
  InsertMonitorConditionOpts,
  MatchRow,
  MonitorAlertRow,
  MonitorConditionRow,
  NotebookRow,
  RecordMatchOpts,
  RunRow,
  TaskQueueRow,
  TrajectoryRow,
  UpsertNotebookOpts,
  UpsertGenerationOpts,
} from "./storage-contracts.js";
import {
  completeStoreTask,
  countPendingStoreTasks,
  dequeueStoreTask,
  enqueueStoreTask,
  failStoreTask,
  getStoreTask,
} from "./storage-task-queue-facade.js";
import {
  appendStoreAgentOutput,
  countStoreCompletedRuns,
  createStoreRun,
  getStoreAgentOutputs,
  getStoreBestGenerationForScenario,
  getStoreBestMatchForScenario,
  getStoreGenerations,
  getStoreMatchesForGeneration,
  getStoreMatchesForRun,
  getStoreRun,
  getStoreScoreTrajectory,
  listStoreRuns,
  listStoreRunsForScenario,
  recordStoreMatch,
  upsertStoreGeneration,
  updateStoreRunStatus,
} from "./storage-generation-run-facade.js";
import {
  getStoreCalibrationExamples,
  getStoreHumanFeedback,
  insertStoreHumanFeedback,
} from "./storage-human-feedback-facade.js";
import {
  deleteStoreNotebook,
  getStoreNotebook,
  listStoreNotebooks,
  upsertStoreNotebook,
} from "./storage-notebook-facade.js";
import {
  countStoreMonitorConditions,
  deactivateStoreMonitorCondition,
  getStoreLatestMonitorAlert,
  getStoreMonitorCondition,
  insertStoreMonitorAlert,
  insertStoreMonitorCondition,
  listStoreMonitorAlerts,
  listStoreMonitorConditions,
} from "./storage-monitor-facade.js";
import { migrateDatabase } from "./storage-migration-workflow.js";

export function configureSqliteDatabase(db: Pick<Database.Database, "pragma">): void {
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON");
}

export class SQLiteStore {
  #db: Database.Database;

  constructor(dbPath: string) {
    this.#db = new Database(dbPath);
    configureSqliteDatabase(this.#db);
  }

  migrate(migrationsDir: string): void {
    migrateDatabase(this.#db, migrationsDir);
  }

  enqueueTask(
    id: string,
    specName: string,
    priority = 0,
    config?: Record<string, unknown>,
    scheduledAt?: string,
  ): void {
    enqueueStoreTask(this.#db, id, specName, priority, config, scheduledAt);
  }

  dequeueTask(): TaskQueueRow | null {
    return dequeueStoreTask(this.#db);
  }

  completeTask(
    taskId: string,
    bestScore: number,
    bestOutput: string,
    totalRounds: number,
    metThreshold: boolean,
    resultJson?: string,
  ): void {
    completeStoreTask(
      this.#db,
      taskId,
      bestScore,
      bestOutput,
      totalRounds,
      metThreshold,
      resultJson,
    );
  }

  failTask(taskId: string, error: string): void {
    failStoreTask(this.#db, taskId, error);
  }

  pendingTaskCount(): number {
    return countPendingStoreTasks(this.#db);
  }

  getTask(taskId: string): TaskQueueRow | null {
    return getStoreTask(this.#db, taskId);
  }

  insertHumanFeedback(
    scenarioName: string,
    agentOutput: string,
    humanScore?: number | null,
    humanNotes = "",
    generationId?: string | null,
  ): number {
    return insertStoreHumanFeedback(
      this.#db,
      scenarioName,
      agentOutput,
      humanScore,
      humanNotes,
      generationId,
    );
  }

  getHumanFeedback(scenarioName: string, limit = 10): HumanFeedbackRow[] {
    return getStoreHumanFeedback(this.#db, scenarioName, limit);
  }

  getCalibrationExamples(scenarioName: string, limit = 5): HumanFeedbackRow[] {
    return getStoreCalibrationExamples(this.#db, scenarioName, limit);
  }

  upsertNotebook(opts: UpsertNotebookOpts): void {
    upsertStoreNotebook(this.#db, opts);
  }

  getNotebook(sessionId: string): NotebookRow | null {
    return getStoreNotebook(this.#db, sessionId);
  }

  listNotebooks(): NotebookRow[] {
    return listStoreNotebooks(this.#db);
  }

  deleteNotebook(sessionId: string): boolean {
    return deleteStoreNotebook(this.#db, sessionId);
  }

  insertMonitorCondition(opts: InsertMonitorConditionOpts): string {
    return insertStoreMonitorCondition(this.#db, opts);
  }

  listMonitorConditions(opts?: { activeOnly?: boolean; scope?: string }): MonitorConditionRow[] {
    return listStoreMonitorConditions(this.#db, opts);
  }

  countMonitorConditions(opts?: { activeOnly?: boolean; scope?: string }): number {
    return countStoreMonitorConditions(this.#db, opts);
  }

  getMonitorCondition(conditionId: string): MonitorConditionRow | null {
    return getStoreMonitorCondition(this.#db, conditionId);
  }

  deactivateMonitorCondition(conditionId: string): boolean {
    return deactivateStoreMonitorCondition(this.#db, conditionId);
  }

  insertMonitorAlert(opts: InsertMonitorAlertOpts): string {
    return insertStoreMonitorAlert(this.#db, opts);
  }

  listMonitorAlerts(opts?: {
    conditionId?: string;
    scope?: string;
    limit?: number;
    since?: string;
  }): MonitorAlertRow[] {
    return listStoreMonitorAlerts(this.#db, opts);
  }

  getLatestMonitorAlert(conditionId: string): MonitorAlertRow | null {
    return getStoreLatestMonitorAlert(this.#db, conditionId);
  }

  createRun(
    runId: string,
    scenario: string,
    generations: number,
    executorMode: string,
    agentProvider = "",
  ): void {
    createStoreRun(this.#db, runId, scenario, generations, executorMode, agentProvider);
  }

  getRun(runId: string): RunRow | null {
    return getStoreRun(this.#db, runId);
  }

  updateRunStatus(runId: string, status: string): void {
    updateStoreRunStatus(this.#db, runId, status);
  }

  upsertGeneration(runId: string, generationIndex: number, opts: UpsertGenerationOpts): void {
    upsertStoreGeneration(this.#db, runId, generationIndex, opts);
  }

  getGenerations(runId: string): GenerationRow[] {
    return getStoreGenerations(this.#db, runId);
  }

  countCompletedRuns(scenario: string): number {
    return countStoreCompletedRuns(this.#db, scenario);
  }

  getBestGenerationForScenario(scenario: string): (GenerationRow & { run_id: string }) | null {
    return getStoreBestGenerationForScenario(this.#db, scenario);
  }

  getBestMatchForScenario(scenario: string): MatchRow | null {
    return getStoreBestMatchForScenario(this.#db, scenario);
  }

  recordMatch(runId: string, generationIndex: number, opts: RecordMatchOpts): void {
    recordStoreMatch(this.#db, runId, generationIndex, opts);
  }

  getMatchesForRun(runId: string): MatchRow[] {
    return getStoreMatchesForRun(this.#db, runId);
  }

  appendAgentOutput(runId: string, generationIndex: number, role: string, content: string): void {
    appendStoreAgentOutput(this.#db, runId, generationIndex, role, content);
  }

  getAgentOutputs(runId: string, generationIndex: number): AgentOutputRow[] {
    return getStoreAgentOutputs(this.#db, runId, generationIndex);
  }

  getScoreTrajectory(runId: string): TrajectoryRow[] {
    return getStoreScoreTrajectory(this.#db, runId);
  }

  listRuns(limit = 50, scenario?: string): RunRow[] {
    return listStoreRuns(this.#db, limit, scenario);
  }

  listRunsForScenario(scenario: string): RunRow[] {
    return listStoreRunsForScenario(this.#db, scenario);
  }

  getMatchesForGeneration(runId: string, generationIndex: number): MatchRow[] {
    return getStoreMatchesForGeneration(this.#db, runId, generationIndex);
  }

  close(): void {
    this.#db.close();
  }
}
