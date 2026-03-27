/**
 * AC-433: new-scenario must materialize runnable custom scenarios.
 *
 * Tests verify that materializeScenario() persists all required artifacts
 * and that the custom-loader can discover and use them.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  materializeScenario,
  type MaterializeResult,
} from "../src/scenarios/materialize.js";
import { loadCustomScenarios } from "../src/scenarios/custom-loader.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-433-test-"));
});
afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// Core materialization
// ---------------------------------------------------------------------------

describe("materializeScenario", () => {
  it("persists spec.json to knowledge/_custom_scenarios/<name>/", async () => {
    const result = await materializeScenario({
      name: "test_task",
      family: "agent_task",
      spec: {
        taskPrompt: "Write a poem",
        rubric: "Evaluate creativity",
        description: "Poetry task",
      },
      knowledgeRoot: tmpDir,
    });

    expect(result.persisted).toBe(true);
    const specPath = join(tmpDir, "_custom_scenarios", "test_task", "spec.json");
    expect(existsSync(specPath)).toBe(true);

    const spec = JSON.parse(readFileSync(specPath, "utf-8"));
    expect(spec.taskPrompt).toBe("Write a poem");
  });

  it("persists scenario_type.txt with correct family marker", async () => {
    await materializeScenario({
      name: "test_sim",
      family: "simulation",
      spec: {
        description: "Test sim",
        taskPrompt: "Simulate",
        rubric: "Evaluate",
        actions: [{ name: "step1", description: "Do it", parameters: {}, preconditions: [], effects: [] }],
      },
      knowledgeRoot: tmpDir,
    });

    const markerPath = join(tmpDir, "_custom_scenarios", "test_sim", "scenario_type.txt");
    expect(existsSync(markerPath)).toBe(true);
    expect(readFileSync(markerPath, "utf-8").trim()).toBe("simulation");
  });

  it("persists agent_task_spec.json for agent_task family", async () => {
    await materializeScenario({
      name: "at_test",
      family: "agent_task",
      spec: {
        taskPrompt: "Do something",
        rubric: "Judge it",
        description: "Test",
      },
      knowledgeRoot: tmpDir,
    });

    const atSpecPath = join(tmpDir, "_custom_scenarios", "at_test", "agent_task_spec.json");
    expect(existsSync(atSpecPath)).toBe(true);
    const atSpec = JSON.parse(readFileSync(atSpecPath, "utf-8"));
    expect(atSpec.task_prompt).toBe("Do something");
    expect(atSpec.judge_rubric).toBe("Judge it");
  });

  it("generates scenario.js for codegen-supported families", async () => {
    const result = await materializeScenario({
      name: "gen_sim",
      family: "simulation",
      spec: {
        description: "Generated sim",
        taskPrompt: "Run sim",
        rubric: "Evaluate",
        actions: [{ name: "act1", description: "Act", parameters: {}, preconditions: [], effects: [] }],
      },
      knowledgeRoot: tmpDir,
    });

    expect(result.generatedSource).toBe(true);
    const jsPath = join(tmpDir, "_custom_scenarios", "gen_sim", "scenario.js");
    expect(existsSync(jsPath)).toBe(true);

    const source = readFileSync(jsPath, "utf-8");
    expect(source).toContain("module.exports");
    expect(source).toContain("gen_sim");
  });

  it("does not generate scenario.js for agent_task (uses ImprovementLoop)", async () => {
    const result = await materializeScenario({
      name: "no_js",
      family: "agent_task",
      spec: { taskPrompt: "Do", rubric: "Judge", description: "Test" },
      knowledgeRoot: tmpDir,
    });

    expect(result.generatedSource).toBe(false);
    const jsPath = join(tmpDir, "_custom_scenarios", "no_js", "scenario.js");
    expect(existsSync(jsPath)).toBe(false);
  });

  it("returns the scenario directory path", async () => {
    const result = await materializeScenario({
      name: "path_test",
      family: "agent_task",
      spec: { taskPrompt: "Do", rubric: "Judge", description: "Test" },
      knowledgeRoot: tmpDir,
    });

    expect(result.scenarioDir).toBe(join(tmpDir, "_custom_scenarios", "path_test"));
  });
});

// ---------------------------------------------------------------------------
// Custom-loader discovery (create → discover)
// ---------------------------------------------------------------------------

describe("materialized scenarios are discoverable", () => {
  it("custom-loader finds materialized agent_task scenario", async () => {
    await materializeScenario({
      name: "disco_task",
      family: "agent_task",
      spec: { taskPrompt: "Find me", rubric: "Judge", description: "Discoverable" },
      knowledgeRoot: tmpDir,
    });

    const loaded = loadCustomScenarios(join(tmpDir, "_custom_scenarios"));
    expect(loaded.has("disco_task")).toBe(true);
    expect(loaded.get("disco_task")!.type).toBe("agent_task");
  });

  it("custom-loader finds materialized simulation with generated source", async () => {
    await materializeScenario({
      name: "disco_sim",
      family: "simulation",
      spec: {
        description: "Discoverable sim",
        taskPrompt: "Sim",
        rubric: "Evaluate",
        actions: [{ name: "a", description: "A", parameters: {}, preconditions: [], effects: [] }],
      },
      knowledgeRoot: tmpDir,
    });

    const loaded = loadCustomScenarios(join(tmpDir, "_custom_scenarios"));
    expect(loaded.has("disco_sim")).toBe(true);
    expect(loaded.get("disco_sim")!.hasGeneratedSource).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// MaterializeResult shape
// ---------------------------------------------------------------------------

describe("MaterializeResult", () => {
  it("has all required fields", async () => {
    const result: MaterializeResult = await materializeScenario({
      name: "shape_test",
      family: "agent_task",
      spec: { taskPrompt: "Do", rubric: "Judge", description: "Test" },
      knowledgeRoot: tmpDir,
    });

    expect(result).toHaveProperty("persisted");
    expect(result).toHaveProperty("generatedSource");
    expect(result).toHaveProperty("scenarioDir");
    expect(result).toHaveProperty("family");
    expect(result).toHaveProperty("name");
    expect(typeof result.persisted).toBe("boolean");
    expect(typeof result.generatedSource).toBe("boolean");
    expect(typeof result.scenarioDir).toBe("string");
  });
});
