/**
 * Tests for AC-366: Training data export with Python-compatible contract.
 * Tests both the helper module and the CLI boundary.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const CLI = join(__dirname, "..", "src", "cli", "index.ts");

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-training-"));
}

function runCli(args: string[], envOverrides: Record<string, string> = {}): { stdout: string; exitCode: number } {
  try {
    const stdout = execFileSync("npx", ["tsx", CLI, ...args], {
      encoding: "utf8",
      timeout: 15000,
      env: { ...process.env, NODE_NO_WARNINGS: "1", ...envOverrides },
    });
    return { stdout, exitCode: 0 };
  } catch (err: unknown) {
    const e = err as { stdout?: string; status?: number };
    return { stdout: e.stdout ?? "", exitCode: e.status ?? 1 };
  }
}

// ---------------------------------------------------------------------------
// Helper module: exportTrainingData
// ---------------------------------------------------------------------------

describe("exportTrainingData helper", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("is importable", async () => {
    const { exportTrainingData } = await import("../src/training/export.js");
    expect(typeof exportTrainingData).toBe("function");
  });

  it("returns empty for nonexistent run", async () => {
    const { exportTrainingData } = await import("../src/training/export.js");
    const { SQLiteStore } = await import("../src/storage/index.js");
    const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));
    const artifacts = new ArtifactStore({
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    expect(exportTrainingData(store, artifacts, { runId: "bogus" })).toEqual([]);
    store.close();
  });

  it("exports Python-compatible context with playbook, hints, and trajectory", async () => {
    const { exportTrainingData } = await import("../src/training/export.js");
    const { SQLiteStore } = await import("../src/storage/index.js");
    const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));
    const artifacts = new ArtifactStore({
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    const playbook = [
      "# Strategy",
      "",
      "<!-- COMPETITOR_HINTS_START -->",
      "Keep pressure on the flag carrier.",
      "<!-- COMPETITOR_HINTS_END -->",
    ].join("\n");
    artifacts.writePlaybook("grid_ctf", playbook);
    store.createRun("run-1", "grid_ctf", 1, "local");
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.65, bestScore: 0.70, elo: 1050,
      wins: 3, losses: 2, gateDecision: "advance", status: "completed",
    });
    store.appendAgentOutput("run-1", 1, "competitor", '{"aggression": 0.6}');
    store.upsertGeneration("run-1", 2, {
      meanScore: 0.75, bestScore: 0.80, elo: 1080,
      wins: 4, losses: 1, gateDecision: "advance", status: "completed",
    });
    store.appendAgentOutput("run-1", 2, "competitor", '{"aggression": 0.7}');

    const records = exportTrainingData(store, artifacts, { runId: "run-1" });
    expect(records.length).toBe(2);

    const rec = records[1];
    expect(rec).toHaveProperty("run_id");
    expect(rec).toHaveProperty("scenario");
    expect(rec).toHaveProperty("generation_index");
    expect(rec).toHaveProperty("strategy");
    expect(rec).toHaveProperty("score");
    expect(rec).toHaveProperty("gate_decision");
    expect("seed" in rec).toBe(false);
    expect(rec.run_id).toBe("run-1");
    expect(rec.score).toBeCloseTo(0.80);
    expect(rec.gate_decision).toBe("advance");
    expect(rec.strategy).toBe('{"aggression": 0.7}');
    expect(rec.context).toEqual({
      playbook: `${playbook}\n`,
      hints: "Keep pressure on the flag carrier.",
      trajectory: [
        { generation_index: 1, best_score: 0.70, gate_decision: "advance" },
        { generation_index: 2, best_score: 0.80, gate_decision: "advance" },
      ],
    });
    store.close();
  });

  it("filters by keptOnly", async () => {
    const { exportTrainingData } = await import("../src/training/export.js");
    const { SQLiteStore } = await import("../src/storage/index.js");
    const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));
    const artifacts = new ArtifactStore({
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    store.createRun("run-1", "grid_ctf", 2, "local");
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
    expect(records.length).toBe(1);
    expect("seed" in records[0]).toBe(false);
    expect(records[0].gate_decision).toBe("advance");
    store.close();
  });

  it("emits separate top-level match records when includeMatches is enabled", async () => {
    const { exportTrainingData } = await import("../src/training/export.js");
    const { SQLiteStore } = await import("../src/storage/index.js");
    const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));
    const artifacts = new ArtifactStore({
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    store.createRun("run-1", "grid_ctf", 1, "local");
    store.upsertGeneration("run-1", 1, {
      meanScore: 0.65, bestScore: 0.70, elo: 1050,
      wins: 2, losses: 1, gateDecision: "advance", status: "completed",
    });
    store.appendAgentOutput("run-1", 1, "competitor", '{"aggression": 0.6}');
    store.recordMatch("run-1", 1, { seed: 42, score: 0.70, passedValidation: true, validationErrors: "", winner: "challenger" });

    const records = exportTrainingData(store, artifacts, { runId: "run-1", includeMatches: true });
    expect(records).toHaveLength(2);
    expect("seed" in records[0]).toBe(false);
    expect("seed" in records[1]).toBe(true);
    const match = records[1];
    if (!("seed" in match)) {
      throw new Error("Expected a match record");
    }
    expect(match).toEqual({
      run_id: "run-1",
      generation_index: 1,
      seed: 42,
      score: 0.70,
      passed_validation: true,
      validation_errors: "",
    });
    store.close();
  });

  it("exports all runs for a scenario without truncating at 1000", async () => {
    const { exportTrainingData } = await import("../src/training/export.js");
    const { SQLiteStore } = await import("../src/storage/index.js");
    const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
    const store = new SQLiteStore(join(dir, "test.db"));
    store.migrate(join(__dirname, "..", "migrations"));
    const artifacts = new ArtifactStore({
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });

    for (let i = 0; i < 1001; i += 1) {
      const runId = `run-${i}`;
      store.createRun(runId, "grid_ctf", 1, "local");
      store.upsertGeneration(runId, 1, {
        meanScore: 0.5,
        bestScore: 0.5,
        elo: 1000,
        wins: 1,
        losses: 0,
        gateDecision: "advance",
        status: "completed",
      });
      store.appendAgentOutput(runId, 1, "competitor", `strategy-${i}`);
    }

    const records = exportTrainingData(store, artifacts, { scenario: "grid_ctf" });
    expect(records).toHaveLength(1001);
    store.close();
  });
});

// ---------------------------------------------------------------------------
// CLI boundary tests
// ---------------------------------------------------------------------------

describe("CLI export-training-data boundary", () => {
  it("help output lists the command", () => {
    const { stdout } = runCli(["--help"]);
    expect(stdout).toContain("export-training-data");
  });

  it("--help shows usage", () => {
    const { stdout, exitCode } = runCli(["export-training-data", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("run-id");
  });

  it("requires --run-id or --scenario", () => {
    const { exitCode } = runCli(["export-training-data"]);
    expect(exitCode).toBe(1);
  });

  it("requires --all-runs with --scenario", () => {
    const { exitCode } = runCli(["export-training-data", "--scenario", "grid_ctf"]);
    expect(exitCode).toBe(1);
  });

  it("exports JSONL with Python-compatible fields from a real run", async () => {
    const dir = makeTempDir();
    const dbPath = join(dir, "test.db");
    const knowledgeRoot = join(dir, "knowledge");

    const { SQLiteStore } = await import("../src/storage/index.js");
    const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
    const store = new SQLiteStore(dbPath);
    store.migrate(join(__dirname, "..", "migrations"));
    const artifacts = new ArtifactStore({
      runsRoot: join(dir, "runs"),
      knowledgeRoot,
    });
    artifacts.writePlaybook(
      "grid_ctf",
      [
        "# Strategy",
        "",
        "<!-- COMPETITOR_HINTS_START -->",
        "Flank early.",
        "<!-- COMPETITOR_HINTS_END -->",
      ].join("\n"),
    );
    store.createRun("cli-run-1", "grid_ctf", 1, "local");
    store.upsertGeneration("cli-run-1", 1, {
      meanScore: 0.65, bestScore: 0.70, elo: 1050,
      wins: 3, losses: 2, gateDecision: "advance", status: "completed",
    });
    store.appendAgentOutput("cli-run-1", 1, "competitor", '{"aggression": 0.6}');
    store.close();

    const { stdout, exitCode } = runCli(
      ["export-training-data", "--run-id", "cli-run-1"],
      {
        AUTOCONTEXT_DB_PATH: dbPath,
        AUTOCONTEXT_RUNS_ROOT: join(dir, "runs"),
        AUTOCONTEXT_KNOWLEDGE_ROOT: knowledgeRoot,
      },
    );
    expect(exitCode).toBe(0);
    const record = JSON.parse(stdout.trim());
    expect(record.run_id).toBe("cli-run-1");
    expect(record.scenario).toBe("grid_ctf");
    expect(record.score).toBeCloseTo(0.70);
    expect(record.context.hints).toBe("Flank early.");
    expect(Array.isArray(record.context.trajectory)).toBe(true);

    rmSync(dir, { recursive: true, force: true });
  });
});
