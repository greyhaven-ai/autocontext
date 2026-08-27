import type Database from "better-sqlite3";

import type {
  AgentOutputRow,
  GenerationRow,
  MatchRow,
  RecordMatchOpts,
  RunRow,
  TrajectoryRow,
  UpsertGenerationOpts,
} from "./storage-contracts.js";
import {
  appendAgentOutputRecord,
  countCompletedRunsForScenario,
  createRunRecord,
  getAgentOutputRecords,
  getBestGenerationForScenarioRecord,
  getBestMatchForScenarioRecord,
  getGenerationRecords,
  getMatchesForGenerationRecord,
  getMatchesForRunRecord,
  getRunRecord,
  getScoreTrajectoryRecords,
  listRunRecords,
  listRunRecordsForScenario,
  recordMatchRecord,
  upsertGenerationRecord,
  updateRunStatusRecord,
} from "./generation-record-store.js";
import { buildScoreTrajectoryRecords } from "./score-trajectory-store.js";

export function createStoreRun(
  db: Database.Database,
  runId: string,
  scenario: string,
  generations: number,
  executorMode: string,
  agentProvider = "",
  minimumGenerations = 1,
): void {
  createRunRecord(
    db,
    runId,
    scenario,
    generations,
    executorMode,
    agentProvider,
    minimumGenerations,
  );
}

export function getStoreRun(
  db: Database.Database,
  runId: string,
): RunRow | null {
  return getRunRecord<RunRow>(db, runId);
}

export function updateStoreRunStatus(
  db: Database.Database,
  runId: string,
  status: string,
): void {
  updateRunStatusRecord(db, runId, status);
}

export function upsertStoreGeneration(
  db: Database.Database,
  runId: string,
  generationIndex: number,
  opts: UpsertGenerationOpts,
): void {
  upsertGenerationRecord(db, runId, generationIndex, opts);
}

export function getStoreGenerations(
  db: Database.Database,
  runId: string,
): GenerationRow[] {
  return getGenerationRecords<GenerationRow>(db, runId);
}

export function countStoreCompletedRuns(
  db: Database.Database,
  scenario: string,
): number {
  return countCompletedRunsForScenario(db, scenario);
}

export function getStoreBestGenerationForScenario(
  db: Database.Database,
  scenario: string,
): (GenerationRow & { run_id: string }) | null {
  return getBestGenerationForScenarioRecord<GenerationRow & { run_id: string }>(db, scenario);
}

export function getStoreBestMatchForScenario(
  db: Database.Database,
  scenario: string,
): MatchRow | null {
  return getBestMatchForScenarioRecord<MatchRow>(db, scenario);
}

export function recordStoreMatch(
  db: Database.Database,
  runId: string,
  generationIndex: number,
  opts: RecordMatchOpts,
): void {
  recordMatchRecord(db, runId, generationIndex, opts);
}

export function getStoreMatchesForRun(
  db: Database.Database,
  runId: string,
): MatchRow[] {
  return getMatchesForRunRecord<MatchRow>(db, runId);
}

export function appendStoreAgentOutput(
  db: Database.Database,
  runId: string,
  generationIndex: number,
  role: string,
  content: string,
): void {
  appendAgentOutputRecord(db, runId, generationIndex, role, content);
}

export function getStoreAgentOutputs(
  db: Database.Database,
  runId: string,
  generationIndex: number,
): AgentOutputRow[] {
  return getAgentOutputRecords<AgentOutputRow>(db, runId, generationIndex);
}

export function getStoreScoreTrajectory(
  db: Database.Database,
  runId: string,
): TrajectoryRow[] {
  return buildScoreTrajectoryRecords(getScoreTrajectoryRecords<GenerationRow>(db, runId));
}

export function listStoreRuns(
  db: Database.Database,
  limit = 50,
  scenario?: string,
): RunRow[] {
  return listRunRecords<RunRow>(db, limit, scenario);
}

export function listStoreRunsForScenario(
  db: Database.Database,
  scenario: string,
): RunRow[] {
  return listRunRecordsForScenario<RunRow>(db, scenario);
}

export function getStoreMatchesForGeneration(
  db: Database.Database,
  runId: string,
  generationIndex: number,
): MatchRow[] {
  return getMatchesForGenerationRecord<MatchRow>(db, runId, generationIndex);
}
