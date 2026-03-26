/**
 * Runtime tests for generated scenario execution via secure-exec isolates.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { executeGeneratedScenarioEntry } from "../src/scenarios/codegen/executor.js";
import { generateSimulationSource } from "../src/scenarios/codegen/simulation-codegen.js";

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-codegen-runtime-"));
}

describe("generated scenario runtime", () => {
  let dir: string;

  beforeEach(() => {
    dir = makeTempDir();
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("loads a persisted generated scenario and executes it through the isolate runtime", async () => {
    const customDir = join(dir, "knowledge", "_custom_scenarios");
    const scenarioDir = join(customDir, "saved_sim");
    mkdirSync(scenarioDir, { recursive: true });

    const spec = {
      description: "Deploy a tiny service",
      environment_description: "Test environment",
      initial_state_description: "Nothing is deployed yet",
      success_criteria: ["service deployed"],
      failure_modes: ["timeout"],
      max_steps: 5,
      actions: [
        {
          name: "provision",
          description: "Provision infrastructure",
          parameters: {},
          preconditions: [],
          effects: ["infra_ready"],
        },
        {
          name: "deploy",
          description: "Deploy the service",
          parameters: {},
          preconditions: ["provision"],
          effects: ["service_ready"],
        },
      ],
    };

    writeFileSync(join(scenarioDir, "scenario_type.txt"), "simulation", "utf-8");
    writeFileSync(
      join(scenarioDir, "spec.json"),
      JSON.stringify({
        name: "saved_sim",
        family: "simulation",
        scenario_type: "simulation",
        ...spec,
      }),
      "utf-8",
    );
    writeFileSync(
      join(scenarioDir, "scenario.js"),
      generateSimulationSource(spec, "saved_sim"),
      "utf-8",
    );

    const result = await executeGeneratedScenarioEntry({
      customDir,
      name: "saved_sim",
      family: "simulation",
    });

    expect(result.score).toBe(1);
    expect(result.stepsExecuted).toBe(2);
    expect(result.records.map((record) => record.action.name)).toEqual(["provision", "deploy"]);
    expect(result.dimensionScores.completion).toBe(1);
  });
});
