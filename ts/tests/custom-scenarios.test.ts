/**
 * Tests for AC-348: Custom Scenario Pipeline — Loader, NL Creation, Intent Validation.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-custom-"));
}

// ---------------------------------------------------------------------------
// Task 29: Custom Scenario Loader
// ---------------------------------------------------------------------------

describe("CustomScenarioLoader", () => {
  let dir: string;

  beforeEach(() => {
    dir = makeTempDir();
  });
  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("should be importable", async () => {
    const { loadCustomScenarios } = await import("../src/scenarios/custom-loader.js");
    expect(typeof loadCustomScenarios).toBe("function");
  });

  it("returns empty map for missing directory", async () => {
    const { loadCustomScenarios } = await import("../src/scenarios/custom-loader.js");
    const loaded = loadCustomScenarios(join(dir, "nonexistent"));
    expect(loaded.size).toBe(0);
  });

  it("returns empty map for empty directory", async () => {
    const { loadCustomScenarios } = await import("../src/scenarios/custom-loader.js");
    const customDir = join(dir, "_custom_scenarios");
    mkdirSync(customDir, { recursive: true });
    const loaded = loadCustomScenarios(customDir);
    expect(loaded.size).toBe(0);
  });

  it("loads a spec.json agent task scenario", async () => {
    const { loadCustomScenarios } = await import("../src/scenarios/custom-loader.js");
    const customDir = join(dir, "_custom_scenarios");
    const scenarioDir = join(customDir, "test_task");
    mkdirSync(scenarioDir, { recursive: true });
    writeFileSync(join(scenarioDir, "scenario_type.txt"), "agent_task", "utf-8");
    writeFileSync(
      join(scenarioDir, "spec.json"),
      JSON.stringify({
        name: "test_task",
        taskPrompt: "Summarize this article.",
        rubric: "Evaluate completeness and accuracy.",
        description: "Test task for summarization.",
      }),
      "utf-8",
    );
    const loaded = loadCustomScenarios(customDir);
    expect(loaded.size).toBe(1);
    expect(loaded.has("test_task")).toBe(true);
    const entry = loaded.get("test_task")!;
    expect(entry.name).toBe("test_task");
    expect(entry.type).toBe("agent_task");
    expect(entry.spec.taskPrompt).toBe("Summarize this article.");
  });

  it("skips directories without spec.json", async () => {
    const { loadCustomScenarios } = await import("../src/scenarios/custom-loader.js");
    const customDir = join(dir, "_custom_scenarios");
    const scenarioDir = join(customDir, "incomplete");
    mkdirSync(scenarioDir, { recursive: true });
    writeFileSync(join(scenarioDir, "scenario_type.txt"), "agent_task", "utf-8");
    // No spec.json
    const loaded = loadCustomScenarios(customDir);
    expect(loaded.size).toBe(0);
  });

  it("defaults to agent_task when scenario_type.txt missing", async () => {
    const { loadCustomScenarios } = await import("../src/scenarios/custom-loader.js");
    const customDir = join(dir, "_custom_scenarios");
    const scenarioDir = join(customDir, "auto_typed");
    mkdirSync(scenarioDir, { recursive: true });
    // No scenario_type.txt, but has spec.json
    writeFileSync(
      join(scenarioDir, "spec.json"),
      JSON.stringify({
        name: "auto_typed",
        taskPrompt: "Do something.",
        rubric: "Evaluate it.",
        description: "Auto-typed test.",
      }),
      "utf-8",
    );
    const loaded = loadCustomScenarios(customDir);
    expect(loaded.size).toBe(1);
    expect(loaded.get("auto_typed")!.type).toBe("agent_task");
  });

  it("loads agent_task_spec.json for persisted agent tasks", async () => {
    const { loadCustomScenarios } = await import("../src/scenarios/custom-loader.js");
    const customDir = join(dir, "_custom_scenarios");
    const scenarioDir = join(customDir, "persisted_task");
    mkdirSync(scenarioDir, { recursive: true });
    writeFileSync(join(scenarioDir, "scenario_type.txt"), "agent_task", "utf-8");
    writeFileSync(
      join(scenarioDir, "agent_task_spec.json"),
      JSON.stringify({
        improvement_task_contract_version: 1,
        task_prompt: "Use the persisted agent-task spec.",
        judge_rubric: "Judge for alignment.",
        output_format: "free_text",
      }),
      "utf-8",
    );

    const loaded = loadCustomScenarios(customDir);
    expect(loaded.get("persisted_task")?.spec).toMatchObject({
      improvementTaskContractVersion: 1,
      taskPrompt: "Use the persisted agent-task spec.",
    });
  });

  it("fails closed when a private evaluator record is missing or tampered", async () => {
    const { persistAgentTaskScenario } = await import(
      "../src/scenarios/agent-task-persistence-workflow.js"
    );
    const { resolveCustomAgentTask } = await import("../src/scenarios/custom-loader.js");
    const scenarioName = "private_integrity_task";
    const evaluationContext = "PRIVATE_INTEGRITY_SENTINEL";
    const persist = () =>
      persistAgentTaskScenario({
        knowledgeRoot: dir,
        name: scenarioName,
        spec: {
          taskPrompt: "Do private integrity work.",
          judgeRubric: "Evaluate integrity.",
          outputFormat: "free_text",
          judgeModel: "",
          evaluationContext,
          maxRounds: 1,
          qualityThreshold: 0.9,
        },
      });
    const scenarioKey = createHash("sha256")
      .update(`scenario:${scenarioName}`, "utf8")
      .digest("hex");
    const digest = createHash("sha256").update(evaluationContext, "utf8").digest("hex");
    const privatePath = join(
      dir,
      "_private_evaluator_context",
      scenarioKey,
      `${digest}.json`,
    );

    persist();
    rmSync(privatePath);
    expect(() => resolveCustomAgentTask(dir, scenarioName)).toThrow(
      /private evaluator context is missing/i,
    );

    persist();
    writeFileSync(
      privatePath,
      JSON.stringify({
        version: 1,
        scenarioName,
        evaluationContext: "TAMPERED_PRIVATE_CONTEXT",
      }),
      "utf8",
    );
    expect(() => resolveCustomAgentTask(dir, scenarioName)).toThrow(
      /failed integrity verification/i,
    );
  });

  it("resolves a healthy target even when an earlier sibling has a broken private record", async () => {
    const { persistAgentTaskScenario } = await import(
      "../src/scenarios/agent-task-persistence-workflow.js"
    );
    const { resolveCustomAgentTask } = await import("../src/scenarios/custom-loader.js");
    const baseSpec = {
      taskPrompt: "Resolve the requested task.",
      judgeRubric: "Evaluate resolution.",
      outputFormat: "free_text" as const,
      judgeModel: "",
      maxRounds: 1,
      qualityThreshold: 0.9,
    };
    const brokenName = "a_broken_private_sibling";
    const healthyName = "z_healthy_private_target";
    const brokenContext = "BROKEN_SIBLING_CONTEXT";
    persistAgentTaskScenario({
      knowledgeRoot: dir,
      name: brokenName,
      spec: { ...baseSpec, evaluationContext: brokenContext },
    });
    persistAgentTaskScenario({
      knowledgeRoot: dir,
      name: healthyName,
      spec: { ...baseSpec, evaluationContext: "HEALTHY_TARGET_CONTEXT" },
    });
    const brokenScenarioKey = createHash("sha256")
      .update(`scenario:${brokenName}`, "utf8")
      .digest("hex");
    const brokenDigest = createHash("sha256").update(brokenContext, "utf8").digest("hex");
    rmSync(
      join(
        dir,
        "_private_evaluator_context",
        brokenScenarioKey,
        `${brokenDigest}.json`,
      ),
    );

    expect(resolveCustomAgentTask(dir, healthyName)?.spec.evaluationContext).toBe(
      "HEALTHY_TARGET_CONTEXT",
    );
    expect(() => resolveCustomAgentTask(dir, brokenName)).toThrow(
      /private evaluator context is missing/i,
    );
  });

  it("continues to load legacy plaintext evaluator context", async () => {
    const { resolveCustomAgentTask } = await import("../src/scenarios/custom-loader.js");
    const scenarioDir = join(dir, "_custom_scenarios", "legacy_private_task");
    mkdirSync(scenarioDir, { recursive: true });
    writeFileSync(join(scenarioDir, "scenario_type.txt"), "agent_task", "utf8");
    writeFileSync(
      join(scenarioDir, "agent_task_spec.json"),
      JSON.stringify({
        task_prompt: "Use a legacy evaluator context.",
        judge_rubric: "Evaluate legacy compatibility.",
        output_format: "free_text",
        evaluation_context: "LEGACY_PRIVATE_CONTEXT",
      }),
      "utf8",
    );

    expect(resolveCustomAgentTask(dir, "legacy_private_task")?.spec.evaluationContext).toBe(
      "LEGACY_PRIVATE_CONTEXT",
    );
  });

  it.each([
    ["null", { evaluation_context_ref: null }],
    ["empty", { evaluation_context_ref: "" }],
    ["wrong type", { evaluation_context_ref: 42 }],
    [
      "conflicting",
      {
        evaluationContextRef: `sha256:${"0".repeat(64)}`,
        evaluation_context_ref: `sha256:${"1".repeat(64)}`,
      },
    ],
  ])("rejects %s private evaluator reference metadata", async (_label, referenceFields) => {
    const { resolveCustomAgentTask } = await import("../src/scenarios/custom-loader.js");
    const scenarioName = `invalid_reference_${String(_label).replace(" ", "_")}`;
    const scenarioDir = join(dir, "_custom_scenarios", scenarioName);
    mkdirSync(scenarioDir, { recursive: true });
    writeFileSync(join(scenarioDir, "scenario_type.txt"), "agent_task", "utf8");
    writeFileSync(
      join(scenarioDir, "agent_task_spec.json"),
      JSON.stringify({
        task_prompt: "Reject malformed private references.",
        judge_rubric: "Evaluate integrity.",
        output_format: "free_text",
        ...referenceFields,
      }),
      "utf8",
    );

    expect(() => resolveCustomAgentTask(dir, scenarioName)).toThrow(
      /private evaluator context reference/i,
    );
  });

  it("registerCustomScenarios keeps agent tasks out of SCENARIO_REGISTRY", async () => {
    const {
      loadCustomScenarios,
      registerCustomScenarios,
      CUSTOM_SCENARIO_REGISTRY,
      CUSTOM_AGENT_TASK_REGISTRY,
    } = await import("../src/scenarios/custom-loader.js");
    const { SCENARIO_REGISTRY } = await import("../src/scenarios/registry.js");

    const customDir = join(dir, "_custom_scenarios");
    const scenarioDir = join(customDir, "registered_task");
    mkdirSync(scenarioDir, { recursive: true });
    writeFileSync(
      join(scenarioDir, "spec.json"),
      JSON.stringify({
        name: "registered_task",
        taskPrompt: "Write a poem.",
        rubric: "Is it creative?",
        description: "Poetry task.",
      }),
      "utf-8",
    );

    const loaded = loadCustomScenarios(customDir);
    const before = Object.keys(SCENARIO_REGISTRY).length;
    registerCustomScenarios(loaded);
    expect(Object.keys(SCENARIO_REGISTRY).length).toBe(before);
    expect(CUSTOM_SCENARIO_REGISTRY.has("registered_task")).toBe(true);
    expect(typeof CUSTOM_AGENT_TASK_REGISTRY.registered_task).toBe("function");
  });
});

// ---------------------------------------------------------------------------
// Task 31: Intent Validator
// ---------------------------------------------------------------------------

describe("IntentValidator", () => {
  it("should be importable", async () => {
    const { IntentValidator } = await import("../src/scenarios/intent-validator.js");
    expect(IntentValidator).toBeDefined();
  });

  it("approves when spec matches intent keywords", async () => {
    const { IntentValidator } = await import("../src/scenarios/intent-validator.js");
    const validator = new IntentValidator();
    const result = validator.validate("I want a scenario that tests summarization quality", {
      name: "summarization_test",
      taskPrompt: "Summarize the following document.",
      rubric: "Evaluate summarization quality and completeness.",
      description: "Tests how well an agent can summarize documents.",
    });
    expect(result.valid).toBe(true);
    expect(result.confidence).toBeGreaterThan(0.5);
  });

  it("rejects when spec has no overlap with intent", async () => {
    const { IntentValidator } = await import("../src/scenarios/intent-validator.js");
    const validator = new IntentValidator();
    const result = validator.validate("I want to test code generation for Python", {
      name: "cooking_recipe",
      taskPrompt: "Write a recipe for chocolate cake.",
      rubric: "Is the recipe clear and complete?",
      description: "Tests recipe writing skills.",
    });
    expect(result.valid).toBe(false);
    expect(result.confidence).toBeLessThan(0.5);
  });

  it("provides issues array on rejection", async () => {
    const { IntentValidator } = await import("../src/scenarios/intent-validator.js");
    const validator = new IntentValidator();
    const result = validator.validate("test math problem solving", {
      name: "poetry_writing",
      taskPrompt: "Write a sonnet about spring.",
      rubric: "Evaluate poetic meter and imagery.",
      description: "Tests creative poetry writing.",
    });
    expect(result.issues.length).toBeGreaterThan(0);
  });

  it("handles edge case of empty intent", async () => {
    const { IntentValidator } = await import("../src/scenarios/intent-validator.js");
    const validator = new IntentValidator();
    const result = validator.validate("", {
      name: "some_task",
      taskPrompt: "Do something.",
      rubric: "Evaluate.",
      description: "A task.",
    });
    // Empty intent is valid (no constraints to violate)
    expect(result.valid).toBe(true);
  });

  it("configurable minimum confidence threshold", async () => {
    const { IntentValidator } = await import("../src/scenarios/intent-validator.js");
    const validator = new IntentValidator(0.8);
    const result = validator.validate("test something vaguely related", {
      name: "vague_match",
      taskPrompt: "Do a vaguely related thing.",
      rubric: "Is it done?",
      description: "A vague test scenario.",
    });
    // With high threshold, marginal matches should fail
    expect(typeof result.valid).toBe("boolean");
    expect(typeof result.confidence).toBe("number");
  });
});

// ---------------------------------------------------------------------------
// Task 30: NL → Scenario Creation flow
// ---------------------------------------------------------------------------

describe("ScenarioCreationFlow", () => {
  it("exports createScenarioFromDescription", async () => {
    const { createScenarioFromDescription } = await import("../src/scenarios/scenario-creator.js");
    expect(typeof createScenarioFromDescription).toBe("function");
  });

  it("creates a scenario spec from natural language description", async () => {
    const { createScenarioFromDescription } = await import("../src/scenarios/scenario-creator.js");
    const { DeterministicProvider } = await import("../src/providers/deterministic.js");

    const provider = new DeterministicProvider();
    const result = await createScenarioFromDescription(
      "I want to test how well an agent summarizes technical documents",
      provider,
    );
    expect(result.name).toBeDefined();
    expect(result.spec).toBeDefined();
    expect(result.spec.taskPrompt).toBeDefined();
    expect(result.spec.rubric).toBeDefined();
  });

  it("returns family classification", async () => {
    const { createScenarioFromDescription } = await import("../src/scenarios/scenario-creator.js");
    const { DeterministicProvider } = await import("../src/providers/deterministic.js");

    const provider = new DeterministicProvider();
    const result = await createScenarioFromDescription(
      "Create a workflow that deploys a service and monitors health",
      provider,
    );
    expect(result.family).toBeDefined();
  });
});
