import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, describe, expect, it } from "vitest";

import { persistMaterializedScenarioArtifacts } from "../src/scenarios/materialize-artifact-persistence.js";
import { resolveCustomAgentTask } from "../src/scenarios/custom-loader.js";
import { loadPrivateEvaluatorContext } from "../src/scenarios/private-evaluator-context-store.js";

describe("materialize artifact persistence", () => {
  const dirs: string[] = [];

  afterEach(() => {
    for (const dir of dirs.splice(0)) {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("writes agent-task artifacts and removes stale scenario.js", () => {
    const knowledgeRoot = mkdtempSync(join(tmpdir(), "ac-materialize-agent-task-"));
    dirs.push(knowledgeRoot);
    const scenarioDir = join(knowledgeRoot, "_custom_scenarios", "materialized_task");
    mkdirSync(scenarioDir, { recursive: true });
    writeFileSync(join(scenarioDir, "scenario.js"), "stale", "utf-8");

    persistMaterializedScenarioArtifacts({
      scenarioDir,
      scenarioType: "agent_task",
      persistedSpec: {
        name: "task",
        family: "agent_task",
        taskPrompt: "Do work",
        evaluationContext: "MATERIALIZED_PRIVATE_SENTINEL",
        evaluation_context: "MATERIALIZED_PRIVATE_SENTINEL",
      },
      family: "agent_task",
      agentTaskFamily: "agent_task",
      agentTaskSpec: {
        improvementTaskContractVersion: 1,
        taskPrompt: "Do work",
        judgeRubric: "Judge work",
        outputFormat: "free_text",
        judgeModel: "",
        evaluationContext: "MATERIALIZED_PRIVATE_SENTINEL",
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
      evaluation_context_ref: expect.stringMatching(/^sha256:[a-f0-9]{64}$/),
    });
    const publicSpec = readFileSync(join(scenarioDir, "spec.json"), "utf-8");
    const publicAgentTaskSpec = readFileSync(join(scenarioDir, "agent_task_spec.json"), "utf-8");
    expect(publicSpec).not.toContain("MATERIALIZED_PRIVATE_SENTINEL");
    expect(publicAgentTaskSpec).not.toContain("MATERIALIZED_PRIVATE_SENTINEL");
    expect(JSON.parse(publicSpec)).toMatchObject({
      evaluationContextRef: expect.stringMatching(/^sha256:[a-f0-9]{64}$/),
    });
    expect(resolveCustomAgentTask(knowledgeRoot, "materialized_task")?.spec.evaluationContext).toBe(
      "MATERIALIZED_PRIVATE_SENTINEL",
    );
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

  it("removes a stale public agent-task artifact before pruning when no source is emitted", () => {
    const knowledgeRoot = mkdtempSync(join(tmpdir(), "ac-materialize-family-change-"));
    dirs.push(knowledgeRoot);
    const scenarioDir = join(knowledgeRoot, "_custom_scenarios", "family_change");

    persistMaterializedScenarioArtifacts({
      scenarioDir,
      scenarioType: "agent_task",
      persistedSpec: { name: "task", family: "agent_task" },
      family: "agent_task",
      agentTaskFamily: "agent_task",
      agentTaskSpec: {
        taskPrompt: "Do private work",
        judgeRubric: "Judge private work",
        outputFormat: "free_text",
        judgeModel: "",
        evaluationContext: "OLD_PRIVATE_CONTEXT",
        maxRounds: 1,
        qualityThreshold: 0.9,
      },
      source: null,
    });
    expect(existsSync(join(scenarioDir, "agent_task_spec.json"))).toBe(true);
    const oldReference = String(
      JSON.parse(readFileSync(join(scenarioDir, "agent_task_spec.json"), "utf8"))
        .evaluation_context_ref,
    );

    persistMaterializedScenarioArtifacts({
      scenarioDir,
      scenarioType: "workflow",
      persistedSpec: { name: "workflow", family: "workflow" },
      family: "workflow",
      agentTaskFamily: "agent_task",
      agentTaskSpec: null,
      source: null,
    });

    expect(existsSync(join(scenarioDir, "agent_task_spec.json"))).toBe(false);
    expect(readFileSync(join(scenarioDir, "spec.json"), "utf8")).not.toContain(
      "evaluationContextRef",
    );
    expect(() =>
      loadPrivateEvaluatorContext({
        knowledgeRoot,
        scenarioName: "family_change",
        reference: oldReference,
      }),
    ).toThrow(/private evaluator context is missing/i);
  });
});
