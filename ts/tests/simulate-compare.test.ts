/**
 * AC-451: simulate compare — structured diff between simulation runs.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  SimulationEngine,
  type SimulationCompareResult,
} from "../src/simulation/engine.js";
import type { LLMProvider } from "../src/types/index.js";

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

  it("reports dimension score deltas", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    await engine.run({ description: "Dim A", saveAs: "dim_a" });
    await engine.run({ description: "Dim B", saveAs: "dim_b" });

    const result = await engine.compare({ left: "dim_a", right: "dim_b" });

    expect(result.dimensionDeltas).toBeDefined();
    expect(typeof result.dimensionDeltas).toBe("object");
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
