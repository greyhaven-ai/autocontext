import { describe, expect, it } from "vitest";

import type { AgentTaskSpec } from "../src/scenarios/agent-task-spec.js";
import {
  applyHealedAgentTaskSpec,
  generateSyntheticSampleInput,
  healAgentTaskSpec,
  needsSampleInput,
  normalizeAgentTaskHealSpec,
} from "../src/scenarios/spec-auto-heal-agent-task.js";

describe("spec auto-heal agent-task workflow", () => {
  it("detects missing sample input for external-data prompts", () => {
    const spec: AgentTaskSpec = {
      taskPrompt: "You will be provided with a dataset. Analyze the trends.",
      judgeRubric: "Evaluate accuracy",
      outputFormat: "free_text",
      judgeModel: "",
      maxRounds: 1,
      qualityThreshold: 0.9,
    };

    expect(needsSampleInput(spec)).toBe(true);
  });

  it("generates deterministic JSON sample input from domain hints", () => {
    const sample = generateSyntheticSampleInput(
      "Analyze customer records and transaction data",
      "Customer analysis",
    );

    expect(JSON.parse(sample)).toBeDefined();
    expect(sample.toLowerCase()).toMatch(/customer|transaction|record|data/);
  });

  it("normalizes snake_case agent-task specs into an AgentTaskSpec", () => {
    const healed = normalizeAgentTaskHealSpec({
      improvement_task_contract_version: 1,
      task_prompt: "You will be provided with an outage log.",
      judge_rubric: "Evaluate accuracy",
      output_format: "code",
      max_rounds: 2,
      quality_threshold: 0.85,
      sample_input: '{"incident":"db-lock"}',
    });

    expect(healed).toMatchObject({
      improvementTaskContractVersion: 1,
      taskPrompt: "You will be provided with an outage log.",
      judgeRubric: "Evaluate accuracy",
      outputFormat: "code",
      maxRounds: 2,
      qualityThreshold: 0.85,
      sampleInput: '{"incident":"db-lock"}',
    });
  });

  it("applies healed agent-task specs using the original casing contract", () => {
    const healedCamel = applyHealedAgentTaskSpec(
      { taskPrompt: "Prompt", rubric: "" },
      {
        improvementTaskContractVersion: 1,
        taskPrompt: "Prompt",
        judgeRubric: "Evaluate",
        outputFormat: "free_text",
        judgeModel: "",
        maxRounds: 1,
        qualityThreshold: 0.9,
        sampleInput: '{"data":[1]}',
      },
    );
    const healedSnake = applyHealedAgentTaskSpec(
      { task_prompt: "Prompt", judge_rubric: "", output_format: "free_text" },
      {
        improvementTaskContractVersion: 1,
        taskPrompt: "Prompt",
        judgeRubric: "Evaluate",
        outputFormat: "free_text",
        judgeModel: "",
        maxRounds: 1,
        qualityThreshold: 0.9,
        sampleInput: '{"data":[1]}',
      },
    );

    expect(healedCamel).toMatchObject({
      improvementTaskContractVersion: 1,
      taskPrompt: "Prompt",
      judgeRubric: "Evaluate",
      rubric: "Evaluate",
      sampleInput: '{"data":[1]}',
    });
    expect(healedSnake).toMatchObject({
      improvement_task_contract_version: 1,
      task_prompt: "Prompt",
      judge_rubric: "Evaluate",
      sample_input: '{"data":[1]}',
    });
  });

  it("heals agent-task specs by adding synthetic sample input only when needed", () => {
    const spec: AgentTaskSpec = {
      taskPrompt: "You will be provided with patient records. Identify drug interactions.",
      judgeRubric: "Evaluate accuracy",
      outputFormat: "free_text",
      judgeModel: "",
      maxRounds: 1,
      qualityThreshold: 0.9,
    };

    const healed = healAgentTaskSpec(spec, "Medical analysis task");

    expect(healed.sampleInput).toBeDefined();
    expect(healed.taskPrompt).toBe(spec.taskPrompt);
  });

  it("never invents replacement input for structured improvement tasks", () => {
    const spec: AgentTaskSpec = {
      improvementTaskContractVersion: 1,
      taskPrompt: "Using the provided dataset, improve the current thesis.",
      judgeRubric: "Evaluate factual support",
      outputFormat: "free_text",
      judgeModel: "",
      maxRounds: 2,
      qualityThreshold: 0.9,
    };

    expect(needsSampleInput(spec)).toBe(true);
    expect(healAgentTaskSpec(spec)).toBe(spec);
    expect(healAgentTaskSpec(spec).sampleInput).toBeUndefined();
  });
});
