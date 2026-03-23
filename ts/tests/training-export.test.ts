/**
 * Tests for AC-366: Training data export + distillation parity decisions.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-training-"));
}

// ---------------------------------------------------------------------------
// Training data export module
// ---------------------------------------------------------------------------

describe("exportTrainingData", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("should be importable", async () => {
    const { exportTrainingData } = await import("../src/training/export.js");
    expect(typeof exportTrainingData).toBe("function");
  });

  it("returns empty array for nonexistent run", async () => {
    const { exportTrainingData } = await import("../src/training/export.js");
    const { SQLiteStore } = await import("../src/storage/index.js");

    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));
    const records = exportTrainingData(store, { runId: "nonexistent" });
    expect(records).toEqual([]);
    store.close();
  });

  it("exports training records for a run", async () => {
    const { exportTrainingData } = await import("../src/training/export.js");
    const { SQLiteStore } = await import("../src/storage/index.js");

    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));

    store.createRun("run-1", "grid_ctf", 2, "local");
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.65, bestScore: 0.70, elo: 1050,
      wins: 3, losses: 2, gateDecision: "advance", status: "completed",
    });
    store.appendAgentOutput("run-1", 1, "competitor", '{"aggression": 0.6}');
    store.upsertGeneration("run-1", 2, {
      meanScore: 0.75, bestScore: 0.80, elo: 1100,
      wins: 4, losses: 1, gateDecision: "advance", status: "completed",
    });
    store.appendAgentOutput("run-1", 2, "competitor", '{"aggression": 0.7}');

    const records = exportTrainingData(store, { runId: "run-1" });
    expect(records.length).toBe(2);
    expect(records[0].runId).toBe("run-1");
    expect(records[0].generationIndex).toBe(1);
    expect(records[0].bestScore).toBeCloseTo(0.70);
    expect(records[0].strategy).toBeDefined();
    store.close();
  });

  it("filters by kept_only (advance decisions only)", async () => {
    const { exportTrainingData } = await import("../src/training/export.js");
    const { SQLiteStore } = await import("../src/storage/index.js");

    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));

    store.createRun("run-1", "grid_ctf", 3, "local");
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.65, bestScore: 0.70, elo: 1050,
      wins: 3, losses: 2, gateDecision: "advance", status: "completed",
    });
    store.appendAgentOutput("run-1", 1, "competitor", '{"aggression": 0.6}');
    store.upsertGeneration("run-1", 2, {
      meanScore: 0.55, bestScore: 0.60, elo: 1020,
      wins: 2, losses: 3, gateDecision: "rollback", status: "completed",
    });
    store.appendAgentOutput("run-1", 2, "competitor", '{"aggression": 0.9}');

    const records = exportTrainingData(store, { runId: "run-1", keptOnly: true });
    expect(records.length).toBe(1);
    expect(records[0].gateDecision).toBe("advance");
    store.close();
  });

  it("includes match records when includeMatches is true", async () => {
    const { exportTrainingData } = await import("../src/training/export.js");
    const { SQLiteStore } = await import("../src/storage/index.js");

    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));

    store.createRun("run-1", "grid_ctf", 1, "local");
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.65, bestScore: 0.70, elo: 1050,
      wins: 2, losses: 1, gateDecision: "advance", status: "completed",
    });
    store.appendAgentOutput("run-1", 1, "competitor", '{"aggression": 0.6}');
    store.recordMatch("run-1", 1, { seed: 42, score: 0.70, passedValidation: true, validationErrors: "", winner: "challenger" });
    store.recordMatch("run-1", 1, { seed: 43, score: 0.60, passedValidation: true, validationErrors: "", winner: "incumbent" });

    const records = exportTrainingData(store, { runId: "run-1", includeMatches: true });
    expect(records.length).toBe(1);
    expect(records[0].matches).toHaveLength(2);
    expect(records[0].matches![0].seed).toBe(42);
    store.close();
  });
});

// ---------------------------------------------------------------------------
// CLI command
// ---------------------------------------------------------------------------

describe("CLI export-training-data", () => {
  it("help output includes export-training-data", async () => {
    const { execFileSync } = await import("node:child_process");
    const CLI = join(__dirname, "..", "src", "cli", "index.ts");
    const result = execFileSync("npx", ["tsx", CLI, "--help"], {
      encoding: "utf-8",
      timeout: 10000,
      env: { ...process.env, NODE_NO_WARNINGS: "1" },
    });
    expect(result).toContain("export-training-data");
  });

  it("export-training-data --help shows options", async () => {
    const { execFileSync } = await import("node:child_process");
    const CLI = join(__dirname, "..", "src", "cli", "index.ts");
    let stdout = "";
    try {
      stdout = execFileSync("npx", ["tsx", CLI, "export-training-data", "--help"], {
        encoding: "utf-8",
        timeout: 10000,
        env: { ...process.env, NODE_NO_WARNINGS: "1" },
      });
    } catch (err: unknown) {
      stdout = (err as { stdout?: string }).stdout ?? "";
    }
    expect(stdout).toContain("run-id");
  });
});
