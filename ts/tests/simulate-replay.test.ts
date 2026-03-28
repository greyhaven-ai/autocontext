/**
 * AC-450: simulate replay — re-execute saved simulations.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync, readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  SimulationEngine,
  type SimulationResult,
} from "../src/simulation/engine.js";
import type { LLMProvider } from "../src/types/index.js";

function mockProvider(): LLMProvider {
  const spec = JSON.stringify({
    description: "Test simulation",
    environment_description: "Test env",
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
  tmpDir = mkdtempSync(join(tmpdir(), "ac-450-test-"));
});
afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// Replay from saved simulation
// ---------------------------------------------------------------------------

describe("simulate replay", () => {
  it("replays a previously saved simulation", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    // First: run and save
    const original = await engine.run({
      description: "Deploy pipeline simulation",
      saveAs: "deploy_test",
    });
    expect(original.status).toBe("completed");
    expect(existsSync(join(original.artifacts.scenarioDir, "report.json"))).toBe(true);

    // Replay
    const replay = await engine.replay({ id: "deploy_test" });
    expect(replay.status).toBe("completed");
    expect(replay.name).toBe("deploy_test");
    expect(replay.family).toBe(original.family);
    expect(typeof replay.summary.score).toBe("number");
  });

  it("replay produces same score with same seed (deterministic)", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    const original = await engine.run({
      description: "Deterministic test",
      saveAs: "determ_test",
    });

    const replay = await engine.replay({ id: "determ_test" });

    // Same generated code + same seed = same score
    expect(replay.summary.score).toBe(original.summary.score);
  });

  it("replay with variable overrides changes the run", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    await engine.run({
      description: "Override test",
      saveAs: "override_test",
    });

    const replay = await engine.replay({
      id: "override_test",
      variables: { custom_param: 42 },
    });

    expect(replay.status).toBe("completed");
    expect(replay.variables.custom_param).toBe(42);
  });

  it("replay with different maxSteps", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    await engine.run({
      description: "Steps test",
      saveAs: "steps_test",
    });

    const replay = await engine.replay({
      id: "steps_test",
      maxSteps: 3,
    });

    expect(replay.status).toBe("completed");
  });

  it("replay persists its own report", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    await engine.run({
      description: "Persist test",
      saveAs: "persist_test",
    });

    const replay = await engine.replay({ id: "persist_test" });

    // Replay should have its own report
    expect(replay.artifacts.reportPath).toBeTruthy();
    expect(existsSync(replay.artifacts.reportPath!)).toBe(true);

    const saved = JSON.parse(readFileSync(replay.artifacts.reportPath!, "utf-8"));
    expect(saved.name).toBe("persist_test");
  });

  it("fails with clear error for nonexistent simulation", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    const result = await engine.replay({ id: "nonexistent" });
    expect(result.status).toBe("failed");
    expect(result.error).toContain("not found");
  });

  it("includes original vs replay comparison data", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);

    await engine.run({
      description: "Compare test",
      saveAs: "compare_test",
    });

    const replay = await engine.replay({ id: "compare_test" });

    expect(replay.replayOf).toBe("compare_test");
    expect(replay.originalScore).toBeDefined();
    expect(typeof replay.originalScore).toBe("number");
    expect(typeof replay.scoreDelta).toBe("number");
  });
});
