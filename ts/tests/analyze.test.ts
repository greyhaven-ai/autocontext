/**
 * AC-448: First-class `analyze` surface.
 *
 * Tests the analysis engine that interprets completed runs, missions,
 * simulations, and investigations — producing structured explanations
 * with attribution, regressions, and uncertainty.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import {
  AnalysisEngine,
  type AnalysisResult,
} from "../src/analysis/engine.js";
import { SQLiteStore } from "../src/storage/index.js";
import { MissionManager } from "../src/mission/manager.js";

let tmpDir: string;
const MIGRATIONS_DIR = join(import.meta.dirname, "..", "migrations");
const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-448-test-"));
});
afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

// Helper: write a simulation report artifact
function writeSimReport(name: string, data: Record<string, unknown>): string {
  const dir = join(tmpDir, "_simulations", name);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "report.json"), JSON.stringify(data, null, 2), "utf-8");
  return dir;
}

// Helper: write an investigation report artifact
function writeInvReport(name: string, data: Record<string, unknown>): string {
  const dir = join(tmpDir, "_investigations", name);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "report.json"), JSON.stringify(data, null, 2), "utf-8");
  return dir;
}

function createEngine() {
  return new AnalysisEngine({
    knowledgeRoot: tmpDir,
    runsRoot: join(tmpDir, "runs"),
    dbPath: join(tmpDir, "autocontext.sqlite3"),
  });
}

// ---------------------------------------------------------------------------
// Single-target analysis
// ---------------------------------------------------------------------------

describe("AnalysisEngine — single target", () => {
  it("analyzes a simulation result", () => {
    writeSimReport("deploy_sim", {
      name: "deploy_sim", family: "simulation", status: "completed",
      summary: { score: 0.85, reasoning: "Good", dimensionScores: { completion: 0.9, recovery: 0.7 } },
      assumptions: ["Bounded to 10 steps"],
      warnings: ["Model-driven result"],
    });

    const engine = createEngine();
    const result = engine.analyze({ id: "deploy_sim", type: "simulation" });

    expect(result.target.type).toBe("simulation");
    expect(result.target.id).toBe("deploy_sim");
    expect(result.mode).toBe("single");
    expect(result.summary.headline).toBeTruthy();
    expect(typeof result.summary.confidence).toBe("number");
    expect(result.findings.length).toBeGreaterThan(0);
  });

  it("analyzes an investigation result", () => {
    writeInvReport("checkout_rca", {
      name: "checkout_rca", family: "investigation", status: "completed",
      question: "Why did conversion drop?",
      hypotheses: [
        { statement: "Config change", confidence: 0.74, status: "supported" },
        { statement: "Traffic spike", confidence: 0.2, status: "contradicted" },
      ],
      evidence: [
        { id: "e1", summary: "Error spike at 14:23", supports: ["h0"] },
      ],
      conclusion: { bestExplanation: "Config change", confidence: 0.74, limitations: [] },
    });

    const engine = createEngine();
    const result = engine.analyze({ id: "checkout_rca", type: "investigation" });

    expect(result.target.type).toBe("investigation");
    expect(result.findings.some((f) => f.kind === "conclusion")).toBe(true);
  });

  it("returns error for nonexistent artifact", () => {
    const engine = createEngine();
    const result = engine.analyze({ id: "nonexistent", type: "simulation" });

    expect(result.summary.headline).toContain("not found");
    expect(result.limitations.length).toBeGreaterThan(0);
  });
  it("analyzes a real run from SQLite + runsRoot artifacts", () => {
    mkdirSync(join(tmpDir, "runs", "run_123"), { recursive: true });
    writeFileSync(
      join(tmpDir, "runs", "run_123", "session_report.md"),
      "## Summary\n\nBalanced exploration improved the best match.",
      "utf-8",
    );

    const store = new SQLiteStore(join(tmpDir, "autocontext.sqlite3"));
    store.migrate(MIGRATIONS_DIR);
    store.createRun("run_123", "grid_ctf", 3, "local", "anthropic");
    store.upsertGeneration("run_123", 1, {
      meanScore: 0.62,
      bestScore: 0.81,
      elo: 1012,
      wins: 3,
      losses: 1,
      gateDecision: "advance",
      status: "completed",
      durationSeconds: 4,
      dimensionSummaryJson: JSON.stringify({ completion: 0.86, recovery: 0.52 }),
      scoringBackend: "elo",
      ratingUncertainty: 0.12,
    });
    store.updateRunStatus("run_123", "completed");
    store.close();

    const engine = createEngine();
    const result = engine.analyze({ id: "run_123", type: "run" });

    expect(result.target.type).toBe("run");
    expect(result.summary.headline).toContain("run_123");
    expect(result.findings.length).toBeGreaterThan(0);
    expect(result.limitations.some((l) => l.includes("session report"))).toBe(true);
  });

  it("analyzes a real mission from mission DB + checkpoints", () => {
    const manager = new MissionManager(join(tmpDir, "autocontext.sqlite3"));
    const missionId = manager.create({
      name: "Ship login",
      goal: "Implement OAuth",
      budget: { maxSteps: 4 },
    });
    manager.advance(missionId, "Inspect auth flow");
    const subgoalId = manager.addSubgoal(missionId, { description: "Wire OAuth callback" });
    manager.updateSubgoalStatus(subgoalId, "completed");
    manager.setStatus(missionId, "completed");
    manager.saveCheckpoint(missionId, join(tmpDir, "runs", "missions", missionId, "checkpoints"));
    manager.close();

    const engine = createEngine();
    const result = engine.analyze({ id: missionId, type: "mission" });

    expect(result.target.type).toBe("mission");
    expect(result.summary.headline).toContain("Ship login");
    expect(result.findings.some((f) => f.statement.includes("completed"))).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Compare mode
// ---------------------------------------------------------------------------

describe("AnalysisEngine — compare", () => {
  it("compares two simulation results", () => {
    writeSimReport("sim_a", {
      name: "sim_a", family: "simulation", status: "completed",
      summary: { score: 0.6, reasoning: "Mediocre", dimensionScores: { completion: 0.5, recovery: 0.7 } },
    });
    writeSimReport("sim_b", {
      name: "sim_b", family: "simulation", status: "completed",
      summary: { score: 0.9, reasoning: "Great", dimensionScores: { completion: 0.95, recovery: 0.85 } },
    });

    const engine = createEngine();
    const result = engine.compare({
      left: { id: "sim_a", type: "simulation" },
      right: { id: "sim_b", type: "simulation" },
    });

    expect(result.mode).toBe("compare");
    expect(result.summary.headline).toBeTruthy();
    expect(result.findings.some((f) => f.kind === "improvement" || f.kind === "regression" || f.kind === "driver")).toBe(true);
    expect(result.attribution).toBeDefined();
    expect(result.attribution!.topFactors.length).toBeGreaterThan(0);
  });

  it("identifies regressions in compare mode", () => {
    writeSimReport("before", {
      name: "before", family: "simulation", status: "completed",
      summary: { score: 0.9, dimensionScores: { completion: 0.95, recovery: 0.85 } },
    });
    writeSimReport("after", {
      name: "after", family: "simulation", status: "completed",
      summary: { score: 0.5, dimensionScores: { completion: 0.4, recovery: 0.6 } },
    });

    const engine = createEngine();
    const result = engine.compare({
      left: { id: "before", type: "simulation" },
      right: { id: "after", type: "simulation" },
    });

    expect(result.regressions.length).toBeGreaterThan(0);
  });

  it("fails honestly for incompatible types", () => {
    writeSimReport("sim", {
      name: "sim", family: "simulation", status: "completed",
      summary: { score: 0.8 },
    });
    writeInvReport("inv", {
      name: "inv", family: "investigation", status: "completed",
      conclusion: { bestExplanation: "X", confidence: 0.7 },
    });

    const engine = createEngine();
    const result = engine.compare({
      left: { id: "sim", type: "simulation" },
      right: { id: "inv", type: "investigation" },
    });

    expect(result.limitations.some((l) => l.toLowerCase().includes("different") || l.toLowerCase().includes("type"))).toBe(true);
    expect(result.summary.headline).toContain("unavailable");
    expect(result.findings).toHaveLength(0);
    expect(result.regressions).toHaveLength(0);
    expect(result.attribution).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// AnalysisResult contract
// ---------------------------------------------------------------------------

describe("AnalysisResult contract", () => {
  it("has all required fields per AC-448", () => {
    writeSimReport("shape_test", {
      name: "shape_test", family: "simulation", status: "completed",
      summary: { score: 0.75, dimensionScores: {} },
    });

    const engine = createEngine();
    const result: AnalysisResult = engine.analyze({ id: "shape_test", type: "simulation" });

    expect(result).toHaveProperty("id");
    expect(result).toHaveProperty("target");
    expect(result).toHaveProperty("mode");
    expect(result).toHaveProperty("summary");
    expect(result).toHaveProperty("findings");
    expect(result).toHaveProperty("regressions");
    expect(result).toHaveProperty("limitations");
    expect(result).toHaveProperty("artifacts");

    expect(typeof result.summary.headline).toBe("string");
    expect(typeof result.summary.confidence).toBe("number");
    expect(Array.isArray(result.findings)).toBe(true);
    expect(Array.isArray(result.regressions)).toBe(true);
    expect(Array.isArray(result.limitations)).toBe(true);
  });
});

describe("analyze CLI integration", () => {
  it("analyzes a real run through the CLI using runsRoot + dbPath", () => {
    mkdirSync(join(tmpDir, "runs", "run_cli"), { recursive: true });
    writeFileSync(
      join(tmpDir, "runs", "run_cli", "session_report.md"),
      "## Summary\n\nBalanced exploration improved the best match.",
      "utf-8",
    );

    const store = new SQLiteStore(join(tmpDir, "autocontext.sqlite3"));
    store.migrate(MIGRATIONS_DIR);
    store.createRun("run_cli", "grid_ctf", 2, "local", "anthropic");
    store.upsertGeneration("run_cli", 1, {
      meanScore: 0.64,
      bestScore: 0.79,
      elo: 1008,
      wins: 3,
      losses: 1,
      gateDecision: "advance",
      status: "completed",
      durationSeconds: 3,
      dimensionSummaryJson: JSON.stringify({ completion: 0.82 }),
      scoringBackend: "elo",
      ratingUncertainty: 0.11,
    });
    store.updateRunStatus("run_cli", "completed");
    store.close();

    const result = spawnSync("npx", ["tsx", CLI, "analyze", "--id", "run_cli", "--type", "run", "--json"], {
      cwd: tmpDir,
      encoding: "utf-8",
      timeout: 15000,
      env: { ...process.env, NODE_NO_WARNINGS: "1" },
    });

    expect(result.status).toBe(0);
    const parsed = JSON.parse(result.stdout);
    expect(parsed.target.type).toBe("run");
    expect(parsed.summary.headline).toContain("run_cli");
  }, 15000);
});
