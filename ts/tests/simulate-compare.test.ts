/**
 * AC-451: simulate compare — structured diff between simulation runs.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import {
  SimulationEngine,
  type SimulationResult,
  type SimulationCompareResult,
} from "../src/simulation/engine.js";
import type { LLMProvider } from "../src/types/index.js";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");
const SANITIZED_KEYS = [
  "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AUTOCONTEXT_API_KEY",
  "AUTOCONTEXT_AGENT_API_KEY", "AUTOCONTEXT_PROVIDER", "AUTOCONTEXT_AGENT_PROVIDER",
  "AUTOCONTEXT_DB_PATH", "AUTOCONTEXT_RUNS_ROOT", "AUTOCONTEXT_KNOWLEDGE_ROOT",
  "AUTOCONTEXT_CONFIG_DIR", "AUTOCONTEXT_AGENT_DEFAULT_MODEL", "AUTOCONTEXT_MODEL",
];

function mockProvider(): LLMProvider {
  const spec = JSON.stringify({
    description: "Test simulation",
    environment_description: "Env",
    initial_state_description: "Start",
    success_criteria: ["done"],
    failure_modes: ["timeout"],
    max_steps: 10,
    actions: [
      { name: "step_a", description: "A", parameters: {}, preconditions: [], effects: ["a_done"] },
      { name: "step_b", description: "B", parameters: {}, preconditions: ["step_a"], effects: ["b_done"] },
    ],
  });
  return {
    complete: async () => ({ text: spec }),
    defaultModel: () => "test-model",
  } as unknown as LLMProvider;
}

function buildEnv(overrides: Record<string, string> = {}): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env, NODE_NO_WARNINGS: "1" };
  for (const key of SANITIZED_KEYS) delete env[key];
  return { ...env, ...overrides };
}

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-451-test-"));
});
afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// Simulation compare
// ---------------------------------------------------------------------------

describe("simulate compare", () => {
  it("compares two saved simulations", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    await engine.run({ description: "First sim", saveAs: "sim_a" });
    await engine.run({ description: "Second sim", saveAs: "sim_b" });

    const result = await engine.compare({ left: "sim_a", right: "sim_b" });

    expect(result.status).toBe("completed");
    expect(result.left.name).toBe("sim_a");
    expect(result.right.name).toBe("sim_b");
    expect(typeof result.scoreDelta).toBe("number");
  });

  it("reports variable deltas between simulations", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    await engine.run({ description: "Sim A", saveAs: "var_a", variables: { threshold: 0.5 } });
    await engine.run({ description: "Sim B", saveAs: "var_b", variables: { threshold: 0.9 } });

    const result = await engine.compare({ left: "var_a", right: "var_b" });

    expect(result.variableDeltas).toBeDefined();
    expect(result.variableDeltas.threshold).toBeDefined();
    expect(result.variableDeltas.threshold.left).toBe(0.5);
    expect(result.variableDeltas.threshold.right).toBe(0.9);
  });

  it("compares an original simulation against a replay artifact by replay id", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    await engine.run({ description: "Replay base", saveAs: "cmp_replay", variables: { max_steps: 2 } });
    const replay = await engine.replay({ id: "cmp_replay", variables: { max_steps: 1 } });

    const result = await engine.compare({ left: "cmp_replay", right: replay.id });

    expect(result.status).toBe("completed");
    expect(result.right.name).toBe(replay.id);
    expect(result.variableDeltas.max_steps.right).toBe(1);
  });

  it("reports dimension score deltas", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    await engine.run({ description: "Dim A", saveAs: "dim_a" });
    await engine.run({ description: "Dim B", saveAs: "dim_b" });

    const result = await engine.compare({ left: "dim_a", right: "dim_b" });

    expect(result.dimensionDeltas).toBeDefined();
    expect(typeof result.dimensionDeltas).toBe("object");
  });

  it("includes sweep-cell variables when comparing swept simulations", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    await engine.run({
      description: "Sweep left",
      saveAs: "sweep_left",
      sweep: [{ name: "max_steps", values: [1, 2] }],
    });
    await engine.run({
      description: "Sweep right",
      saveAs: "sweep_right",
      sweep: [{ name: "max_steps", values: [3, 4] }],
    });

    const result = await engine.compare({ left: "sweep_left", right: "sweep_right" });

    expect(result.status).toBe("completed");
    expect(result.variableDeltas.max_steps).toBeDefined();
    expect(result.variableDeltas.max_steps.left).toEqual([1, 2]);
    expect(result.variableDeltas.max_steps.right).toEqual([3, 4]);
    expect(result.likelyDrivers).toContain("max_steps");
  });

  it("identifies which variable changes likely drove outcome differences", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    await engine.run({ description: "Driver A", saveAs: "drv_a", variables: { x: 1, y: 2 } });
    await engine.run({ description: "Driver B", saveAs: "drv_b", variables: { x: 10, y: 2 } });

    const result = await engine.compare({ left: "drv_a", right: "drv_b" });

    expect(result.likelyDrivers).toBeDefined();
    expect(Array.isArray(result.likelyDrivers)).toBe(true);
  });

  it("produces human-readable summary", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    await engine.run({ description: "Sum A", saveAs: "sum_a" });
    await engine.run({ description: "Sum B", saveAs: "sum_b" });

    const result = await engine.compare({ left: "sum_a", right: "sum_b" });

    expect(typeof result.summary).toBe("string");
    expect(result.summary.length).toBeGreaterThan(0);
  });

  it("persists compare report", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    await engine.run({ description: "Rep A", saveAs: "rep_a" });
    await engine.run({ description: "Rep B", saveAs: "rep_b" });

    const result = await engine.compare({ left: "rep_a", right: "rep_b" });

    expect(result.reportPath).toBeTruthy();
    expect(existsSync(result.reportPath!)).toBe(true);
  });

  it("fails with clear error for nonexistent simulation", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    await engine.run({ description: "Exists", saveAs: "exists" });

    const result = await engine.compare({ left: "exists", right: "nonexistent" });

    expect(result.status).toBe("failed");
    expect(result.error).toContain("not found");
  });

  it("fails when comparing simulations from different families", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    const leftDir = join(tmpDir, "_simulations", "left_sim");
    const rightDir = join(tmpDir, "_simulations", "right_sim");
    mkdirSync(leftDir, { recursive: true });
    mkdirSync(rightDir, { recursive: true });

    const leftReport: SimulationResult = {
      id: "sim_left",
      name: "left_sim",
      family: "simulation",
      status: "completed",
      description: "left",
      assumptions: [],
      variables: { threshold: 0.5 },
      summary: { score: 0.4, reasoning: "left", dimensionScores: { completion: 0.4 } },
      artifacts: { scenarioDir: leftDir, reportPath: join(leftDir, "report.json") },
      warnings: [],
    };
    const rightReport: SimulationResult = {
      id: "sim_right",
      name: "right_sim",
      family: "coordination",
      status: "completed",
      description: "right",
      assumptions: [],
      variables: { threshold: 0.9 },
      summary: { score: 0.8, reasoning: "right", dimensionScores: { coordination: 0.8 } },
      artifacts: { scenarioDir: rightDir, reportPath: join(rightDir, "report.json") },
      warnings: [],
    };

    writeFileSync(join(leftDir, "report.json"), JSON.stringify(leftReport, null, 2), "utf-8");
    writeFileSync(join(rightDir, "report.json"), JSON.stringify(rightReport, null, 2), "utf-8");

    const result = await engine.compare({ left: "left_sim", right: "right_sim" });

    expect(result.status).toBe("failed");
    expect(result.error).toContain("different families");
  });
});

// ---------------------------------------------------------------------------
// SimulationCompareResult contract
// ---------------------------------------------------------------------------

describe("SimulationCompareResult shape", () => {
  it("has all required fields", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    await engine.run({ description: "Shape A", saveAs: "shp_a" });
    await engine.run({ description: "Shape B", saveAs: "shp_b" });

    const result: SimulationCompareResult = await engine.compare({ left: "shp_a", right: "shp_b" });

    expect(result).toHaveProperty("status");
    expect(result).toHaveProperty("left");
    expect(result).toHaveProperty("right");
    expect(result).toHaveProperty("scoreDelta");
    expect(result).toHaveProperty("variableDeltas");
    expect(result).toHaveProperty("dimensionDeltas");
    expect(result).toHaveProperty("likelyDrivers");
    expect(result).toHaveProperty("summary");
  });
});

describe("simulate compare CLI integration", () => {
  it("fails clearly when only one compare side is provided", () => {
    const cwd = mkdtempSync(join(tmpdir(), "ac-451-cli-"));
    try {
      const result = spawnSync("npx", ["tsx", CLI, "simulate", "--compare-left", "sim_a"], {
        cwd,
        encoding: "utf-8",
        env: buildEnv(),
        timeout: 15000,
      });

      expect(result.status).toBe(1);
      expect(result.stderr).toContain("--compare-left and --compare-right must be provided together");
    } finally {
      rmSync(cwd, { recursive: true, force: true });
    }
  });
});
