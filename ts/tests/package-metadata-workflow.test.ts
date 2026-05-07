import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { SQLiteStore } from "../src/storage/index.js";
import {
  bestStrategyForScenario,
  displayNameForScenario,
  packageMetadataPath,
  readPackageMetadata,
  writePackageMetadata,
} from "../src/knowledge/package-metadata.js";

describe("package metadata workflow", () => {
  let dir: string;
  let store: SQLiteStore;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-package-metadata-"));
    store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(import.meta.dirname, "..", "migrations"));
  });

  afterEach(() => {
    store.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("writes and reads persisted package metadata", () => {
    writePackageMetadata(dir, "grid_ctf", {
      best_score: 0.91,
      metadata: { completed_runs: 3 },
    });

    expect(packageMetadataPath(dir, "grid_ctf")).toContain("package_metadata.json");
    expect(readPackageMetadata(dir, "grid_ctf")).toMatchObject({
      best_score: 0.91,
      metadata: { completed_runs: 3 },
    });
  });

  it("ignores package metadata with non-object JSON shape", () => {
    const path = packageMetadataPath(dir, "grid_ctf");
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, JSON.stringify(["not", "metadata"]), "utf-8");

    expect(readPackageMetadata(dir, "grid_ctf")).toEqual({});
  });

  it("prefers parsed best-match strategy and falls back to persisted metadata on invalid JSON", () => {
    store.createRun("run-1", "grid_ctf", 1, "local");
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.6,
      bestScore: 0.8,
      elo: 1100,
      wins: 1,
      losses: 0,
      gateDecision: "advance",
      status: "completed",
    });
    store.updateRunStatus("run-1", "completed");
    store.recordMatch("run-1", 1, {
      seed: 42,
      score: 0.8,
      passedValidation: true,
      validationErrors: "",
      strategyJson: '{"aggression":0.8}',
    });

    expect(bestStrategyForScenario(store, "grid_ctf", { best_strategy: { aggression: 0.1 } })).toEqual({ aggression: 0.8 });
    expect(displayNameForScenario("grid_ctf")).toBe("Grid Ctf");

    store.recordMatch("run-1", 1, {
      seed: 43,
      score: 0.9,
      passedValidation: true,
      validationErrors: "",
      strategyJson: "{bad-json",
    });

    expect(bestStrategyForScenario(store, "grid_ctf", { best_strategy: { fallback: true } })).toEqual({ fallback: true });
  });

  it("falls back when best-match strategy JSON is not an object", () => {
    store.createRun("run-2", "grid_ctf", 1, "local");
    store.upsertGeneration("run-2", 1, {
      meanScore: 0.6,
      bestScore: 0.8,
      elo: 1100,
      wins: 1,
      losses: 0,
      gateDecision: "advance",
      status: "completed",
    });
    store.updateRunStatus("run-2", "completed");
    store.recordMatch("run-2", 1, {
      seed: 44,
      score: 0.8,
      passedValidation: true,
      validationErrors: "",
      strategyJson: JSON.stringify(["not", "strategy"]),
    });

    expect(bestStrategyForScenario(store, "grid_ctf", { best_strategy: { fallback: true } })).toEqual({ fallback: true });
  });
});
