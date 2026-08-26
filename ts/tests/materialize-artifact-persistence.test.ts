import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, describe, expect, it } from "vitest";

import { persistMaterializedScenarioArtifacts } from "../src/scenarios/materialize-artifact-persistence.js";

describe("materialize artifact persistence", () => {
  const dirs: string[] = [];

  afterEach(() => {
    for (const dir of dirs.splice(0)) {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("writes agent-task artifacts and removes stale scenario.js", () => {
    const scenarioDir = mkdtempSync(join(tmpdir(), "ac-materialize-agent-task-"));
    dirs.push(scenarioDir);
    writeFileSync(join(scenarioDir, "scenario.js"), "stale", "utf-8");

    persistMaterializedScenarioArtifacts({
      scenarioDir,
      scenarioType: "agent_task",
      persistedSpec: { name: "task", family: "agent_task", taskPrompt: "Do work" },
      family: "agent_task",
      agentTaskFamily: "agent_task",
      agentTaskSpec: {
        improvementTaskContractVersion: 1,
        taskPrompt: "Do work",
        judgeRubric: "Judge work",
        outputFormat: "free_text",
        judgeModel: "",
        maxRounds: 1,
        qualityThreshold: 0.9,
      },
      source: null,
    });

    expect(existsSync(join(scenarioDir, "scenario_type.txt"))).toBe(true);
    expect(existsSync(join(scenarioDir, "spec.json"))).toBe(true);
    expect(existsSync(join(scenarioDir, "agent_task_spec.json"))).toBe(true);
    expect(existsSync(join(scenarioDir, "scenario.js"))).toBe(false);
    expect(
      JSON.parse(readFileSync(join(scenarioDir, "agent_task_spec.json"), "utf-8")),
    ).toMatchObject({
      improvement_task_contract_version: 1,
      task_prompt: "Do work",
      judge_rubric: "Judge work",
    });
  });

  it("writes generated source artifacts and removes stale agent_task_spec.json", () => {
    const scenarioDir = mkdtempSync(join(tmpdir(), "ac-materialize-codegen-"));
    dirs.push(scenarioDir);
    writeFileSync(join(scenarioDir, "agent_task_spec.json"), "stale", "utf-8");

    persistMaterializedScenarioArtifacts({
      scenarioDir,
      scenarioType: "simulation",
      persistedSpec: { name: "sim", family: "simulation", description: "Generated sim" },
      family: "simulation",
      agentTaskFamily: "agent_task",
      agentTaskSpec: null,
      source: "module.exports = { scenario: {} }",
    });

    expect(existsSync(join(scenarioDir, "scenario_type.txt"))).toBe(true);
    expect(existsSync(join(scenarioDir, "spec.json"))).toBe(true);
    expect(existsSync(join(scenarioDir, "scenario.js"))).toBe(true);
    expect(existsSync(join(scenarioDir, "agent_task_spec.json"))).toBe(false);
    expect(readFileSync(join(scenarioDir, "scenario.js"), "utf-8")).toContain("module.exports");
  });
});
