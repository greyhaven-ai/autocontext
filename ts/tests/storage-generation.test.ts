/**
 * Tests for AC-342 Task 2: Storage Extensions — generation loop CRUD.
 *
 * Covers: createRun, upsertGeneration, recordMatch, appendAgentOutput,
 * getScoreTrajectory, getMatchesForRun, getGenerations.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";
import { SQLiteStore } from "../src/storage/index.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-storage-"));
}

function createStore(dir: string): SQLiteStore {
  const dbPath = join(dir, "test.db");
  const store = new SQLiteStore(dbPath);
  const tsMigrations = join(__dirname, "..", "migrations");
  store.migrate(tsMigrations);
  return store;
}

describe("createRun", () => {
  let dir: string;
  let store: SQLiteStore;

  beforeEach(() => {
    dir = makeTempDir();
    store = createStore(dir);
  });

  afterEach(() => {
    store.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("should insert a new run", () => {
    store.createRun("run-1", "grid_ctf", 5, "local");
    const run = store.getRun("run-1");
    expect(run).toBeDefined();
    expect(run!.run_id).toBe("run-1");
    expect(run!.scenario).toBe("grid_ctf");
    expect(run!.target_generations).toBe(5);
    expect(run!.executor_mode).toBe("local");
    expect(run!.status).toBe("running");
  });

  it("should be idempotent (INSERT OR IGNORE)", () => {
    store.createRun("run-1", "grid_ctf", 5, "local");
    store.createRun("run-1", "grid_ctf", 10, "local");
    const run = store.getRun("run-1");
    expect(run!.target_generations).toBe(5); // first insert wins
  });

  it("should accept optional agent_provider", () => {
    store.createRun("run-2", "othello", 3, "local", "deterministic");
    const run = store.getRun("run-2");
    expect(run!.agent_provider).toBe("deterministic");
  });
});

describe("upsertGeneration", () => {
  let dir: string;
  let store: SQLiteStore;

  beforeEach(() => {
    dir = makeTempDir();
    store = createStore(dir);
    store.createRun("run-1", "grid_ctf", 5, "local");
  });

  afterEach(() => {
    store.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("should insert a new generation", () => {
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.65,
      bestScore: 0.70,
      elo: 1050.0,
      wins: 3,
      losses: 2,
      gateDecision: "advance",
      status: "completed",
    });
    const gens = store.getGenerations("run-1");
    expect(gens).toHaveLength(1);
    expect(gens[0].mean_score).toBeCloseTo(0.65);
    expect(gens[0].best_score).toBeCloseTo(0.70);
    expect(gens[0].elo).toBeCloseTo(1050.0);
    expect(gens[0].gate_decision).toBe("advance");
  });

  it("should upsert (update on conflict)", () => {
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.50,
      bestScore: 0.55,
      elo: 1000.0,
      wins: 1,
      losses: 4,
      gateDecision: "retry",
      status: "completed",
    });
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.70,
      bestScore: 0.80,
      elo: 1100.0,
      wins: 4,
      losses: 1,
      gateDecision: "advance",
      status: "completed",
    });
    const gens = store.getGenerations("run-1");
    expect(gens).toHaveLength(1);
    expect(gens[0].best_score).toBeCloseTo(0.80);
    expect(gens[0].gate_decision).toBe("advance");
  });

  it("should accept optional duration_seconds", () => {
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.65,
      bestScore: 0.70,
      elo: 1050.0,
      wins: 3,
      losses: 2,
      gateDecision: "advance",
      status: "completed",
      durationSeconds: 42.5,
    });
    const gens = store.getGenerations("run-1");
    expect(gens[0].duration_seconds).toBeCloseTo(42.5);
  });

  it("should accept optional scoring_backend and rating_uncertainty", () => {
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.65,
      bestScore: 0.70,
      elo: 1050.0,
      wins: 3,
      losses: 2,
      gateDecision: "advance",
      status: "completed",
      scoringBackend: "glicko",
      ratingUncertainty: 75.0,
    });
    const gens = store.getGenerations("run-1");
    expect(gens[0].scoring_backend).toBe("glicko");
    expect(gens[0].rating_uncertainty).toBeCloseTo(75.0);
  });
});

describe("recordMatch", () => {
  let dir: string;
  let store: SQLiteStore;

  beforeEach(() => {
    dir = makeTempDir();
    store = createStore(dir);
    store.createRun("run-1", "grid_ctf", 5, "local");
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.65,
      bestScore: 0.70,
      elo: 1050.0,
      wins: 3,
      losses: 2,
      gateDecision: "advance",
      status: "completed",
    });
  });

  afterEach(() => {
    store.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("should insert a match", () => {
    store.recordMatch("run-1", 1, {
      seed: 42,
      score: 0.80,
      passedValidation: true,
      validationErrors: "",
    });
    const matches = store.getMatchesForRun("run-1");
    expect(matches).toHaveLength(1);
    expect(matches[0].seed).toBe(42);
    expect(matches[0].score).toBeCloseTo(0.80);
    expect(matches[0].passed_validation).toBe(1);
  });

  it("should accept optional winner, strategy_json, replay_json", () => {
    store.recordMatch("run-1", 1, {
      seed: 42,
      score: 0.90,
      passedValidation: true,
      validationErrors: "",
      winner: "challenger",
      strategyJson: '{"aggression": 0.8}',
      replayJson: '[{"turn": 1}]',
    });
    const matches = store.getMatchesForRun("run-1");
    expect(matches[0].winner).toBe("challenger");
    expect(matches[0].strategy_json).toBe('{"aggression": 0.8}');
    expect(matches[0].replay_json).toContain("turn");
  });

  it("should insert multiple matches for same generation", () => {
    for (let i = 0; i < 3; i++) {
      store.recordMatch("run-1", 1, {
        seed: 100 + i,
        score: 0.5 + i * 0.1,
        passedValidation: true,
        validationErrors: "",
      });
    }
    const matches = store.getMatchesForRun("run-1");
    expect(matches).toHaveLength(3);
  });
});

describe("appendAgentOutput", () => {
  let dir: string;
  let store: SQLiteStore;

  beforeEach(() => {
    dir = makeTempDir();
    store = createStore(dir);
    store.createRun("run-1", "grid_ctf", 5, "local");
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.65,
      bestScore: 0.70,
      elo: 1050.0,
      wins: 3,
      losses: 2,
      gateDecision: "advance",
      status: "completed",
    });
  });

  afterEach(() => {
    store.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("should insert agent output", () => {
    store.appendAgentOutput("run-1", 1, "competitor", '{"aggression": 0.8}');
    const outputs = store.getAgentOutputs("run-1", 1);
    expect(outputs).toHaveLength(1);
    expect(outputs[0].role).toBe("competitor");
    expect(outputs[0].content).toBe('{"aggression": 0.8}');
  });

  it("should append multiple outputs for different roles", () => {
    store.appendAgentOutput("run-1", 1, "competitor", '{"x": 1}');
    store.appendAgentOutput("run-1", 1, "analyst", "Analysis text");
    store.appendAgentOutput("run-1", 1, "coach", "Coach update");
    const outputs = store.getAgentOutputs("run-1", 1);
    expect(outputs).toHaveLength(3);
    const roles = outputs.map((o: Record<string, unknown>) => o.role);
    expect(roles).toContain("competitor");
    expect(roles).toContain("analyst");
    expect(roles).toContain("coach");
  });
});

describe("getScoreTrajectory", () => {
  let dir: string;
  let store: SQLiteStore;

  beforeEach(() => {
    dir = makeTempDir();
    store = createStore(dir);
    store.createRun("run-1", "grid_ctf", 5, "local");
  });

  afterEach(() => {
    store.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("should return empty array for run with no completed generations", () => {
    const traj = store.getScoreTrajectory("run-1");
    expect(traj).toEqual([]);
  });

  it("should return trajectory with deltas", () => {
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.50,
      bestScore: 0.55,
      elo: 1000.0,
      wins: 2,
      losses: 3,
      gateDecision: "retry",
      status: "completed",
    });
    store.upsertGeneration("run-1", 2, {
      meanScore: 0.65,
      bestScore: 0.70,
      elo: 1050.0,
      wins: 3,
      losses: 2,
      gateDecision: "advance",
      status: "completed",
    });
    store.upsertGeneration("run-1", 3, {
      meanScore: 0.80,
      bestScore: 0.85,
      elo: 1100.0,
      wins: 4,
      losses: 1,
      gateDecision: "advance",
      status: "completed",
    });

    const traj = store.getScoreTrajectory("run-1");
    expect(traj).toHaveLength(3);
    expect(traj[0].delta).toBeCloseTo(0.55); // first gen delta from 0
    expect(traj[1].delta).toBeCloseTo(0.15); // 0.70 - 0.55
    expect(traj[2].delta).toBeCloseTo(0.15); // 0.85 - 0.70
  });

  it("should only include completed generations", () => {
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.50,
      bestScore: 0.55,
      elo: 1000.0,
      wins: 2,
      losses: 3,
      gateDecision: "advance",
      status: "completed",
    });
    store.upsertGeneration("run-1", 2, {
      meanScore: 0.0,
      bestScore: 0.0,
      elo: 1000.0,
      wins: 0,
      losses: 0,
      gateDecision: "",
      status: "running",
    });

    const traj = store.getScoreTrajectory("run-1");
    expect(traj).toHaveLength(1);
  });

  it("should include scoring_backend and rating_uncertainty when present", () => {
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.65,
      bestScore: 0.70,
      elo: 1050.0,
      wins: 3,
      losses: 2,
      gateDecision: "advance",
      status: "completed",
      scoringBackend: "glicko",
      ratingUncertainty: 75.0,
    });
    const traj = store.getScoreTrajectory("run-1");
    expect(traj[0].scoring_backend).toBe("glicko");
    expect(traj[0].rating_uncertainty).toBeCloseTo(75.0);
  });
});
