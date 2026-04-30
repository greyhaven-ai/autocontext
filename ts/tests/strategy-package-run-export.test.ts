import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { join } from "node:path";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";

import { ArtifactStore } from "../src/knowledge/artifact-store.js";
import { exportStrategyPackage } from "../src/knowledge/package.js";
import { writePackageMetadata } from "../src/knowledge/package-metadata.js";
import { SQLiteStore } from "../src/storage/index.js";

describe("strategy package run export", () => {
  let dir: string;
  let store: SQLiteStore;
  let artifacts: ArtifactStore;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-strategy-package-run-"));
    store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(import.meta.dirname, "..", "migrations"));
    artifacts = new ArtifactStore({
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    artifacts.writePlaybook("grid_ctf", "## Lesson\n\nTake the safe lane.");
  });

  afterEach(() => {
    store.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("exports package scores and strategy from the requested run id", () => {
    store.createRun("run-low", "grid_ctf", 1, "local");
    store.upsertGeneration("run-low", 1, {
      meanScore: 0.3,
      bestScore: 0.4,
      elo: 1040,
      wins: 1,
      losses: 0,
      gateDecision: "advance",
      status: "completed",
    });
    store.updateRunStatus("run-low", "completed");
    store.recordMatch("run-low", 1, {
      seed: 1,
      score: 0.4,
      passedValidation: true,
      validationErrors: "",
      strategyJson: '{"aggression":0.4}',
    });

    store.createRun("run-high", "grid_ctf", 1, "local");
    store.upsertGeneration("run-high", 1, {
      meanScore: 0.7,
      bestScore: 0.9,
      elo: 1300,
      wins: 3,
      losses: 0,
      gateDecision: "advance",
      status: "completed",
    });
    store.updateRunStatus("run-high", "completed");
    store.recordMatch("run-high", 1, {
      seed: 2,
      score: 0.9,
      passedValidation: true,
      validationErrors: "",
      strategyJson: '{"aggression":0.9}',
    });

    const pkg = exportStrategyPackage({
      scenarioName: "grid_ctf",
      sourceRunId: "run-low",
      artifacts,
      store,
    });

    expect(pkg.best_score).toBe(0.4);
    expect(pkg.best_elo).toBe(1040);
    expect(pkg.best_strategy).toEqual({ aggression: 0.4 });
    expect(pkg.metadata).toMatchObject({
      source_run_id: "run-low",
      source_generation: 1,
    });
  });

  it("rejects run-specific exports when the run has no generation metrics", () => {
    store.createRun("run-empty", "grid_ctf", 1, "local");

    expect(() =>
      exportStrategyPackage({
        scenarioName: "grid_ctf",
        sourceRunId: "run-empty",
        artifacts,
        store,
      }),
    ).toThrow("No generation metrics found for run run-empty");
  });

  it("does not mix persisted scenario strategy into a run-specific export", () => {
    writePackageMetadata(artifacts.knowledgeRoot, "grid_ctf", {
      best_strategy: { aggression: 0.99 },
      best_score: 0.99,
      best_elo: 1900,
      metadata: {
        source_run_id: "another-run",
        source_generation: 7,
      },
    });
    store.createRun("run-no-strategy", "grid_ctf", 1, "local");
    store.upsertGeneration("run-no-strategy", 1, {
      meanScore: 0.3,
      bestScore: 0.4,
      elo: 1040,
      wins: 1,
      losses: 0,
      gateDecision: "advance",
      status: "completed",
    });

    const pkg = exportStrategyPackage({
      scenarioName: "grid_ctf",
      sourceRunId: "run-no-strategy",
      artifacts,
      store,
    });

    expect(pkg.best_score).toBe(0.4);
    expect(pkg.best_elo).toBe(1040);
    expect(pkg.best_strategy).toBeNull();
    expect(pkg.metadata).toMatchObject({
      source_run_id: "run-no-strategy",
      source_generation: 1,
    });
  });
});
