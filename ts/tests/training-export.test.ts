/**
 * Tests for AC-366: Training data export + distillation parity decisions.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";
import { SQLiteStore } from "../src/storage/index.js";
import { ArtifactStore } from "../src/knowledge/artifact-store.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-training-"));
}

function runCli(args: string[], envOverrides: Record<string, string> = {}): { stdout: string; exitCode: number } {
  try {
    const stdout = execFileSync("npx", ["tsx", CLI, ...args], {
      encoding: "utf8",
      timeout: 10000,
      env: { ...process.env, NODE_NO_WARNINGS: "1", ...envOverrides },
    });
    return { stdout, exitCode: 0 };
  } catch (err: unknown) {
    const e = err as { stdout?: string; status?: number };
    return { stdout: e.stdout ?? "", exitCode: e.status ?? 1 };
  }
}

function seedRunFixtures(dir: string): {
  store: SQLiteStore;
  artifacts: ArtifactStore;
  dbPath: string;
  runsRoot: string;
  knowledgeRoot: string;
} {
  const dbPath = join(dir, "test.db");
  const runsRoot = join(dir, "runs");
  const knowledgeRoot = join(dir, "knowledge");
  const store = new SQLiteStore(dbPath);
  store.migrate(join(__dirname, "..", "migrations"));
  const artifacts = new ArtifactStore({ runsRoot, knowledgeRoot });
  return { store, artifacts, dbPath, runsRoot, knowledgeRoot };
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
    const { store, artifacts } = seedRunFixtures(dir);
    const records = exportTrainingData(store, artifacts, { runId: "nonexistent" });
    expect(records).toEqual([]);
    store.close();
  });

  it("exports Python-compatible strategy records with context", async () => {
    const { exportTrainingData } = await import("../src/training/export.js");
    const { store, artifacts } = seedRunFixtures(dir);

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
    artifacts.writePlaybook(
      "grid_ctf",
      [
        "<!-- PLAYBOOK_START -->",
        "Center pressure plan",
        "<!-- PLAYBOOK_END -->",
        "",
        "<!-- LESSONS_START -->",
        "- Stable wins came from balanced pressure.",
        "<!-- LESSONS_END -->",
        "",
        "<!-- COMPETITOR_HINTS_START -->",
        "- Keep defender coverage above 0.5.",
        "<!-- COMPETITOR_HINTS_END -->",
      ].join("\n"),
    );

    const records = exportTrainingData(store, artifacts, { runId: "run-1" });
    expect(records).toHaveLength(2);
    expect(records[0]).toEqual({
      run_id: "run-1",
      scenario: "grid_ctf",
      generation_index: 1,
      strategy: '{"aggression": 0.6}',
      score: 0.7,
      gate_decision: "advance",
      context: {
        playbook: expect.stringContaining("Center pressure plan"),
        hints: "- Keep defender coverage above 0.5.",
        trajectory: [
          { generation_index: 1, best_score: 0.7, gate_decision: "advance" },
        ],
      },
    });
    expect((records[1] as { context: { trajectory: unknown[] } }).context.trajectory).toEqual([
      { generation_index: 1, best_score: 0.7, gate_decision: "advance" },
      { generation_index: 2, best_score: 0.8, gate_decision: "advance" },
    ]);
    store.close();
  });

  it("filters by kept_only (advance decisions only)", async () => {
    const { exportTrainingData } = await import("../src/training/export.js");
    const { store, artifacts } = seedRunFixtures(dir);

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

    const records = exportTrainingData(store, artifacts, { runId: "run-1", keptOnly: true });
    expect(records).toHaveLength(1);
    expect(records[0]).toMatchObject({ gate_decision: "advance" });
    store.close();
  });

  it("emits separate match records when includeMatches is true", async () => {
    const { exportTrainingData } = await import("../src/training/export.js");
    const { store, artifacts } = seedRunFixtures(dir);

    store.createRun("run-1", "grid_ctf", 1, "local");
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.65, bestScore: 0.70, elo: 1050,
      wins: 2, losses: 1, gateDecision: "advance", status: "completed",
    });
    store.appendAgentOutput("run-1", 1, "competitor", '{"aggression": 0.6}');
    store.recordMatch("run-1", 1, { seed: 42, score: 0.70, passedValidation: true, validationErrors: "", winner: "challenger" });
    store.recordMatch("run-1", 1, { seed: 43, score: 0.60, passedValidation: false, validationErrors: "too aggressive", winner: "incumbent" });

    const records = exportTrainingData(store, artifacts, { runId: "run-1", includeMatches: true });
    expect(records).toHaveLength(3);
    expect(records[0]).toMatchObject({
      run_id: "run-1",
      generation_index: 1,
      strategy: '{"aggression": 0.6}',
    });
    expect(records[1]).toEqual({
      run_id: "run-1",
      generation_index: 1,
      seed: 42,
      score: 0.7,
      passed_validation: true,
      validation_errors: "",
    });
    expect(records[2]).toEqual({
      run_id: "run-1",
      generation_index: 1,
      seed: 43,
      score: 0.6,
      passed_validation: false,
      validation_errors: "too aggressive",
    });
    store.close();
  });

  it("exports all scenario runs without truncating at 1000", async () => {
    const { exportTrainingData } = await import("../src/training/export.js");
    const { store, artifacts } = seedRunFixtures(dir);

    for (let i = 0; i < 1002; i++) {
      const runId = `run-${i}`;
      store.createRun(runId, "grid_ctf", 1, "local");
      store.upsertGeneration(runId, 1, {
        meanScore: 0.5,
        bestScore: 0.5 + (i / 10000),
        elo: 1000 + i,
        wins: 1,
        losses: 0,
        gateDecision: "advance",
        status: "completed",
      });
      store.appendAgentOutput(runId, 1, "competitor", `{"aggression": ${i}}`);
    }

    const records = exportTrainingData(store, artifacts, { scenario: "grid_ctf" });
    expect(records).toHaveLength(1002);
    store.close();
  });
});

// ---------------------------------------------------------------------------
// CLI command
// ---------------------------------------------------------------------------

describe("CLI export-training-data", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("help output includes export-training-data", () => {
    const result = execFileSync("npx", ["tsx", CLI, "--help"], {
      encoding: "utf-8",
      timeout: 10000,
      env: { ...process.env, NODE_NO_WARNINGS: "1" },
    });
    expect(result).toContain("export-training-data");
  });

  it("export-training-data --help shows options", () => {
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
    expect(stdout).toContain("all-runs");
  });

  it("requires --all-runs when exporting by scenario", () => {
    const { stdout, exitCode } = runCli(["export-training-data", "--scenario", "grid_ctf"]);
    expect(exitCode).toBe(1);
    expect(stdout).toBe("");
  });

  it("writes Python-compatible JSONL from the CLI", () => {
    const { store, artifacts, dbPath, runsRoot, knowledgeRoot } = seedRunFixtures(dir);
    const outputPath = join(dir, "training.jsonl");

    store.createRun("run-1", "grid_ctf", 1, "local");
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.65, bestScore: 0.70, elo: 1050,
      wins: 2, losses: 1, gateDecision: "advance", status: "completed",
    });
    store.appendAgentOutput("run-1", 1, "competitor", '{"aggression": 0.6}');
    artifacts.writePlaybook(
      "grid_ctf",
      [
        "<!-- PLAYBOOK_START -->",
        "CLI playbook",
        "<!-- PLAYBOOK_END -->",
        "",
        "<!-- LESSONS_START -->",
        "- Lesson",
        "<!-- LESSONS_END -->",
        "",
        "<!-- COMPETITOR_HINTS_START -->",
        "- Hint",
        "<!-- COMPETITOR_HINTS_END -->",
      ].join("\n"),
    );

    const { exitCode } = runCli([
      "export-training-data",
      "--run-id",
      "run-1",
      "--output",
      outputPath,
      "--runs-root",
      runsRoot,
      "--knowledge-root",
      knowledgeRoot,
    ], {
      AUTOCONTEXT_DB_PATH: dbPath,
    });

    expect(exitCode).toBe(0);
    expect(existsSync(outputPath)).toBe(true);
    const lines = readFileSync(outputPath, "utf-8").trim().split("\n");
    expect(lines).toHaveLength(1);
    const payload = JSON.parse(lines[0]);
    expect(payload).toMatchObject({
      run_id: "run-1",
      scenario: "grid_ctf",
      generation_index: 1,
      strategy: '{"aggression": 0.6}',
      score: 0.7,
      gate_decision: "advance",
    });
    expect(payload.context.playbook).toContain("CLI playbook");
    expect(payload.context.hints).toBe("- Hint");
    store.close();
  });
});
