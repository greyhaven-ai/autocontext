import Database from "better-sqlite3";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  type AgentOutputRow,
  type GenerationRow,
  type MatchRow,
  type RunRow,
  SQLiteStore,
} from "../src/storage/index.js";
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
} from "../src/storage/generation-record-store.js";

const MIGRATIONS_DIR = join(import.meta.dirname, "..", "migrations");

describe("generation record store workflow", () => {
  let dir: string;
  let db: Database.Database;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-generation-store-"));
    const dbPath = join(dir, "test.db");
    const store = new SQLiteStore(dbPath);
    store.migrate(MIGRATIONS_DIR);
    store.close();
    db = new Database(dbPath);
  });

  afterEach(() => {
    db.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("creates runs and generations and returns sorted run/generation records", () => {
    createRunRecord(db, "run-1", "grid_ctf", 3, "local", "deterministic");
    upsertGenerationRecord(db, "run-1", 1, {
      meanScore: 0.6,
      bestScore: 0.7,
      elo: 1050,
      wins: 3,
      losses: 2,
      gateDecision: "advance",
      status: "completed",
      scoringBackend: "glicko",
      ratingUncertainty: 80,
    });
    updateRunStatusRecord(db, "run-1", "completed");

    expect(getRunRecord<RunRow>(db, "run-1")).toMatchObject({
      scenario: "grid_ctf",
      status: "completed",
      agent_provider: "deterministic",
    });
    expect(getGenerationRecords<GenerationRow>(db, "run-1")).toHaveLength(1);
    expect(listRunRecords<RunRow>(db, 10)).toHaveLength(1);
    expect(listRunRecordsForScenario<RunRow>(db, "grid_ctf")).toHaveLength(1);
    expect(countCompletedRunsForScenario(db, "grid_ctf")).toBe(1);

    const trajectory = getScoreTrajectoryRecords<GenerationRow>(db, "run-1");
    expect(trajectory[0]).toMatchObject({
      generation_index: 1,
      delta: 0.7,
      scoring_backend: "glicko",
      rating_uncertainty: 80,
    });
  });

  it("preserves quarantined and evaluator_epoch when a later upsert omits them", () => {
    createRunRecord(db, "run-1", "grid_ctf", 3, "local");
    upsertGenerationRecord(db, "run-1", 1, {
      meanScore: 0.6,
      bestScore: 0.7,
      elo: 1050,
      wins: 3,
      losses: 2,
      gateDecision: "advance",
      status: "completed",
      evaluatorEpoch: "epoch-abc",
      quarantined: 1,
    });
    // Re-upsert the SAME row omitting quarantined + evaluatorEpoch (both default to null).
    upsertGenerationRecord(db, "run-1", 1, {
      meanScore: 0.8,
      bestScore: 0.9,
      elo: 1100,
      wins: 4,
      losses: 2,
      gateDecision: "advance",
      status: "completed",
    });
    let row = getGenerationRecords<GenerationRow>(db, "run-1")[0];
    expect(row.quarantined).toBe(1); // COALESCE keeps the stored marker, not the incoming null
    expect(row.evaluator_epoch).toBe("epoch-abc");

    // An explicit falsy 0 IS distinguishable from omitted-null and overwrites.
    upsertGenerationRecord(db, "run-1", 1, {
      meanScore: 0.8,
      bestScore: 0.9,
      elo: 1100,
      wins: 4,
      losses: 2,
      gateDecision: "advance",
      status: "completed",
      quarantined: 0,
    });
    row = getGenerationRecords<GenerationRow>(db, "run-1")[0];
    expect(row.quarantined).toBe(0);
  });

  it("records matches and agent outputs and returns best/generation-scoped lookups", () => {
    createRunRecord(db, "run-1", "grid_ctf", 3, "local");
    upsertGenerationRecord(db, "run-1", 1, {
      meanScore: 0.6,
      bestScore: 0.7,
      elo: 1050,
      wins: 3,
      losses: 2,
      gateDecision: "advance",
      status: "completed",
    });
    updateRunStatusRecord(db, "run-1", "completed");

    recordMatchRecord(db, "run-1", 1, {
      seed: 42,
      score: 0.9,
      passedValidation: true,
      validationErrors: "",
      winner: "challenger",
      strategyJson: '{"aggression":0.8}',
      replayJson: '[{"turn":1}]',
    });
    appendAgentOutputRecord(db, "run-1", 1, "competitor", '{"aggression":0.8}');

    expect(getMatchesForRunRecord<MatchRow>(db, "run-1")).toHaveLength(1);
    expect(getMatchesForGenerationRecord<MatchRow>(db, "run-1", 1)[0]).toMatchObject({
      winner: "challenger",
      seed: 42,
    });
    expect(getAgentOutputRecords<AgentOutputRow>(db, "run-1", 1)[0]).toMatchObject({
      role: "competitor",
    });
    expect(
      getBestGenerationForScenarioRecord<GenerationRow & { run_id: string }>(db, "grid_ctf"),
    ).toMatchObject({
      run_id: "run-1",
      best_score: 0.7,
    });
    expect(getBestMatchForScenarioRecord<MatchRow>(db, "grid_ctf")).toMatchObject({
      score: 0.9,
      strategy_json: '{"aggression":0.8}',
    });
  });
});
