import { describe, it, expect } from "vitest";
import { mkdtempSync, existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  AgentTaskSpecSchema,
  parseRawSpec,
} from "../src/scenarios/agent-task-spec.js";
import {
  parseAgentTaskSpec,
  SPEC_START,
  SPEC_END,
} from "../src/scenarios/agent-task-designer.js";
import { validateSpec } from "../src/scenarios/agent-task-validator.js";
import { createAgentTask } from "../src/scenarios/agent-task-factory.js";
import { AgentTaskCreator } from "../src/scenarios/agent-task-creator.js";
import type { AgentTaskSpec } from "../src/scenarios/agent-task-spec.js";
import type { LLMProvider, CompletionResult } from "../src/types/index.js";

// --- Helpers ---

const SAMPLE_SPEC: AgentTaskSpec = {
  taskPrompt: "Write a haiku about testing software.",
  judgeRubric:
    "Evaluate on: (1) Format — valid haiku (5-7-5)? (2) Relevance — about testing? (3) Creativity",
  outputFormat: "free_text",
  judgeModel: "claude-sonnet-4-20250514",
  maxRounds: 1,
  qualityThreshold: 0.9,
};

function mockLlmResponse(spec: AgentTaskSpec): string {
  const data: Record<string, unknown> = {
    task_prompt: spec.taskPrompt,
    judge_rubric: spec.judgeRubric,
    output_format: spec.outputFormat,
    judge_model: spec.judgeModel,
  };
  return `Here is the spec:\n${SPEC_START}\n${JSON.stringify(data, null, 2)}\n${SPEC_END}\n`;
}

function makeMockProvider(response = "mock output"): LLMProvider {
  return {
    complete: async () => ({ text: response, model: "mock", usage: { inputTokens: 0, outputTokens: 0 } }) as CompletionResult,
  };
}

// --- Tests ---

describe("AgentTaskSpec", () => {
  it("parses valid spec", () => {
    const spec = parseRawSpec({
      task_prompt: "Do something",
      judge_rubric: "Check quality",
    });
    expect(spec.taskPrompt).toBe("Do something");
    expect(spec.outputFormat).toBe("free_text");
    expect(spec.maxRounds).toBe(1);
    expect(spec.qualityThreshold).toBe(0.9);
  });

  it("rejects empty task_prompt", () => {
    expect(() => parseRawSpec({ task_prompt: "", judge_rubric: "ok" })).toThrow();
  });

  it("rejects invalid output_format", () => {
    expect(() =>
      AgentTaskSpecSchema.parse({
        taskPrompt: "Do something",
        judgeRubric: "Check",
        outputFormat: "invalid",
      }),
    ).toThrow();
  });

  it("accepts optional fields", () => {
    const spec = parseRawSpec({
      task_prompt: "Write about RLMs",
      judge_rubric: "Check accuracy",
      reference_context: "RLM = Recursive Language Model",
      required_concepts: ["context folding"],
      max_rounds: 3,
      quality_threshold: 0.8,
    });
    expect(spec.referenceContext).toBe("RLM = Recursive Language Model");
    expect(spec.requiredConcepts).toEqual(["context folding"]);
    expect(spec.maxRounds).toBe(3);
    expect(spec.qualityThreshold).toBe(0.8);
  });
});

describe("Designer", () => {
  it("parses spec from LLM response with delimiters", () => {
    const response = mockLlmResponse(SAMPLE_SPEC);
    const spec = parseAgentTaskSpec(response);
    expect(spec.taskPrompt).toBe(SAMPLE_SPEC.taskPrompt);
    expect(spec.judgeRubric).toBe(SAMPLE_SPEC.judgeRubric);
  });

  it("throws on missing delimiters", () => {
    expect(() => parseAgentTaskSpec("no delimiters here")).toThrow("does not contain");
  });

  it("handles extra text around delimiters", () => {
    const response = `Some preamble text.\n${mockLlmResponse(SAMPLE_SPEC)}\nSome postscript.`;
    const spec = parseAgentTaskSpec(response);
    expect(spec.taskPrompt).toBe(SAMPLE_SPEC.taskPrompt);
  });
});

describe("Validator", () => {
  it("validates correct spec", () => {
    expect(validateSpec(SAMPLE_SPEC)).toEqual([]);
  });

  it("catches empty rubric", () => {
    const errors = validateSpec({ ...SAMPLE_SPEC, judgeRubric: "" });
    expect(errors.length).toBeGreaterThan(0);
    expect(errors.some((e) => e.includes("judge_rubric") || e.includes("judgeRubric"))).toBe(true);
  });
});

describe("Factory", () => {
  it("creates task with correct properties", () => {
    const task = createAgentTask({ spec: SAMPLE_SPEC, name: "haiku_task" });
    expect(task.name).toBe("haiku_task");
    expect(task.getTaskPrompt({})).toBe(SAMPLE_SPEC.taskPrompt);
    expect(task.getRubric()).toBe(SAMPLE_SPEC.judgeRubric);
    expect(task.describeTask()).toBe(SAMPLE_SPEC.taskPrompt);
  });

  it("initialState includes name and format", () => {
    const task = createAgentTask({ spec: SAMPLE_SPEC, name: "test" });
    const state = task.initialState();
    expect(state.taskName).toBe("test");
    expect(state.outputFormat).toBe("free_text");
  });

  it("prepareContext adds spec fields", async () => {
    const spec: AgentTaskSpec = {
      ...SAMPLE_SPEC,
      referenceContext: "domain knowledge",
      contextPreparation: "load docs",
      referenceSources: ["https://example.com"],
    };
    const task = createAgentTask({ spec, name: "ctx_test" });
    const state = await task.prepareContext!({});
    expect(state.referenceContext).toBe("domain knowledge");
    expect(state.contextPreparation).toBe("load docs");
    expect(state.referenceSources).toEqual(["https://example.com"]);
  });

  it("validateContext catches missing keys", () => {
    const spec: AgentTaskSpec = {
      ...SAMPLE_SPEC,
      requiredContextKeys: ["source_data", "config"],
    };
    const task = createAgentTask({ spec, name: "val_test" });
    const errors = task.validateContext!({});
    expect(errors).toHaveLength(2);
    expect(errors[0]).toContain("source_data");
  });

  it("validateContext passes with all keys present", () => {
    const spec: AgentTaskSpec = {
      ...SAMPLE_SPEC,
      requiredContextKeys: ["source_data"],
    };
    const task = createAgentTask({ spec, name: "val_test" });
    const errors = task.validateContext!({ source_data: "present" });
    expect(errors).toHaveLength(0);
  });

  it("evaluateOutput throws without provider", async () => {
    const task = createAgentTask({ spec: SAMPLE_SPEC, name: "no_provider" });
    await expect(task.evaluateOutput("output", {})).rejects.toThrow("provider required");
  });
});

describe("AgentTaskCreator", () => {
  it("derives name from description", () => {
    const provider = makeMockProvider(mockLlmResponse(SAMPLE_SPEC));
    const creator = new AgentTaskCreator({
      provider,
      knowledgeRoot: "/tmp/unused",
    });
    expect(creator.deriveName("Write a haiku about testing software")).toBe("write_haiku_about");
    expect(creator.deriveName("Create something")).toBe("create_something");
  });

  it("end-to-end: creates task and saves files", async () => {
    const response = mockLlmResponse(SAMPLE_SPEC);
    const provider = makeMockProvider(response);

    const tmpDir = mkdtempSync(join(tmpdir(), "mts-creator-"));
    const creator = new AgentTaskCreator({
      provider,
      knowledgeRoot: tmpDir,
    });

    const task = await creator.create("Write a haiku about testing software");
    expect(task.getTaskPrompt({})).toBe(SAMPLE_SPEC.taskPrompt);
    expect(task.getRubric()).toBe(SAMPLE_SPEC.judgeRubric);

    // Check files were saved
    const name = creator.deriveName("Write a haiku about testing software");
    const scenarioDir = join(tmpDir, "_custom_scenarios", name);
    expect(existsSync(join(scenarioDir, "agent_task_spec.json"))).toBe(true);
    expect(existsSync(join(scenarioDir, "scenario_type.txt"))).toBe(true);
    expect(readFileSync(join(scenarioDir, "scenario_type.txt"), "utf-8")).toBe("agent_task");

    const specData = JSON.parse(readFileSync(join(scenarioDir, "agent_task_spec.json"), "utf-8"));
    expect(specData.task_prompt).toBe(SAMPLE_SPEC.taskPrompt);
    expect(specData.judge_rubric).toBe(SAMPLE_SPEC.judgeRubric);
  });

  it("end-to-end with reference context", async () => {
    const spec: AgentTaskSpec = {
      ...SAMPLE_SPEC,
      taskPrompt: "Write about RLMs",
      judgeRubric: "Check accuracy",
      referenceContext: "RLM = Recursive Language Model",
      referenceSources: ["https://example.com/rlm"],
      requiredConcepts: ["context folding"],
    };
    const response = mockLlmResponse(spec);
    // Need to build a response that includes the extra fields
    const data: Record<string, unknown> = {
      task_prompt: spec.taskPrompt,
      judge_rubric: spec.judgeRubric,
      output_format: spec.outputFormat,
      judge_model: spec.judgeModel,
      reference_context: spec.referenceContext,
      reference_sources: spec.referenceSources,
      required_concepts: spec.requiredConcepts,
    };
    const fullResponse = `${SPEC_START}\n${JSON.stringify(data, null, 2)}\n${SPEC_END}`;
    const provider = makeMockProvider(fullResponse);

    const tmpDir = mkdtempSync(join(tmpdir(), "mts-creator-ref-"));
    const creator = new AgentTaskCreator({ provider, knowledgeRoot: tmpDir });
    await creator.create("Write about recursive language models");

    const name = creator.deriveName("Write about recursive language models");
    const scenarioDir = join(tmpDir, "_custom_scenarios", name);
    const specData = JSON.parse(readFileSync(join(scenarioDir, "agent_task_spec.json"), "utf-8"));
    expect(specData.reference_context).toBe("RLM = Recursive Language Model");
    expect(specData.reference_sources).toEqual(["https://example.com/rlm"]);
    expect(specData.required_concepts).toEqual(["context folding"]);
  });
});
