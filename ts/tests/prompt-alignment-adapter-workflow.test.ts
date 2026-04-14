import { describe, expect, it } from "vitest";

import {
  buildPromptContractShape,
  validatePromptContract,
} from "../src/training/prompt-contract-workflow.js";
import { validatePromptAlignmentReport } from "../src/training/prompt-alignment-validation.js";
import { adaptRuntimePromptBundle } from "../src/training/runtime-prompt-adapter-workflow.js";
import {
  adaptTrainingPromptRecord,
  buildTrainingShareGptExample,
} from "../src/training/training-prompt-adapter-workflow.js";

describe("prompt alignment adapter workflow", () => {
  it("builds prompt contract shape and validates required sections", () => {
    expect(buildPromptContractShape()).toMatchObject({
      systemFields: [
        "scenarioRules",
        "strategyInterface",
        "evaluationCriteria",
        "playbook",
        "trajectory",
      ],
      userFields: ["task"],
    });

    expect(validatePromptContract({
      system: "## Scenario Rules\nRules\n\n## Evaluation Criteria\nScore",
      user: "Produce strategy",
    })).toEqual({ valid: true, errors: [] });

    expect(validatePromptContract({ system: "Rules only", user: "Go" }).valid).toBe(false);
  });

  it("adapts runtime/training prompts and reports structural mismatches", () => {
    const runtime = adaptRuntimePromptBundle({
      competitor: "## Scenario Rules\nRules\n\n## Evaluation Criteria\nScore\n\n## Your Task\nProduce strategy",
    });
    const training = adaptTrainingPromptRecord({
      scenario: "grid_ctf",
      strategy: '{"move":"north"}',
      score: 0.9,
      context: {
        scenarioRules: "Rules",
        evaluationCriteria: "Score",
        playbook: "Hold center",
        trajectory: [{ generation_index: 1, best_score: 0.65, gate_decision: "advance" }],
      },
    });

    expect(runtime.user).toBe("Produce strategy");
    expect(training.system).toContain("## Current Playbook");
    expect(training.system).toContain("Generation 1: score=0.6500, gate=advance");
    expect(buildTrainingShareGptExample({
      scenario: "grid_ctf",
      strategy: '{"move":"north"}',
      score: 0.9,
      context: {
        scenarioRules: "Rules",
        evaluationCriteria: "Score",
      },
    }).conversations[2]).toEqual({ from: "gpt", value: '{"move":"north"}' });

    const report = validatePromptAlignmentReport({
      trainingPrompt: training,
      runtimePrompt: runtime,
    });
    expect(report.aligned).toBe(false);
    expect(report.mismatches.some((mismatch) => mismatch.includes("Playbook"))).toBe(true);
  });
});
